/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * Licensed under the Oculus SDK License Agreement (the "License");
 * you may not use the Oculus SDK except in compliance with the License,
 * which is provided at the time of installation or download, or which
 * otherwise accompanies this software in either electronic or hard copy form.
 *
 * You may obtain a copy of the License at
 * https://developer.oculus.com/licenses/oculussdk/
 *
 * Unless required by applicable law or agreed to in writing, the Oculus SDK
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
/*******************************************************************************

Filename    :   Main.cpp
Content     :   OpenXR sample app to showcase the use of controller and haptics extensions
Created     :   Dec 2020
Authors     :   -
Language    :   C++

*******************************************************************************/

#include <array>
#include <cstdio>
#include <chrono>
#include <iomanip>
#include <locale>
#include <sstream>

#include "XrApp.h"
#include "GUI/VRMenu.h"
#include "GUI/VRMenuObject.h"
#include "Input/TinyUI.h"
#include "Remotes.h"

// A read-only coordinate monitor. The HUD follows the viewer, but all displayed
// positions are measured in CurrentSpace, never relative to the viewer.
class OculusTeleopApp : public OVRFW::XrApp {
   public:
    OculusTeleopApp() {
        BackgroundColor = OVR::Vector4f(0.025f, 0.035f, 0.055f, 1.0f);
    }

    void GetInitialSceneUri(std::string& uri) const override {
        uri.clear();
    }

    std::vector<const char*> GetExtensions() override {
        auto extensions = XrApp::GetExtensions();
        for (const auto& property : GetXrExtensionProperties()) {
            if (std::string(property.extensionName) == XR_FB_TOUCH_CONTROLLER_PRO_EXTENSION_NAME) {
                extensions.push_back(XR_FB_TOUCH_CONTROLLER_PRO_EXTENSION_NAME);
                touchProSupported_ = true;
            }
        }
        return extensions;
    }

    std::unordered_map<XrPath, std::vector<XrActionSuggestedBinding>> GetSuggestedBindings(
        XrInstance instance) override {
        // Retain the SDK's standard Touch bindings and simple-controller fallback.
        auto bindings = XrApp::GetSuggestedBindings(instance);
        if (touchProSupported_) {
            XrPath touch = XR_NULL_PATH;
            XrPath pro = XR_NULL_PATH;
            OXR(xrStringToPath(instance, "/interaction_profiles/oculus/touch_controller", &touch));
            OXR(xrStringToPath(instance, "/interaction_profiles/facebook/touch_controller_pro", &pro));
            bindings[pro] = bindings.at(touch);
        }
        return bindings;
    }

    bool AppInit(const xrJava* context) override {
        ui_.BackgroundColor = OVR::Vector4f(0.04f, 0.06f, 0.09f, 0.95f);
        if (!ui_.Init(context, GetFileSys(), false)) {
            return false;
        }
        // Labels, not interactive buttons. English uses the SDK's bundled font.
        const char* text[] = {
            "Head / Hand Coordinates",
            "Reference space: waiting",
            "HEAD: waiting",
            "LEFT: waiting",
            "RIGHT: waiting",
            "X/Y: fixed axes | Z: up | metres"
        };
        for (size_t i = 0; i < labels_.size(); ++i) {
            labels_[i] = ui_.AddLabel(text[i], {0.0f, 0.0f, 0.0f}, {1100.0f, 115.0f});
            if (!labels_[i]) {
                return false;
            }
            labels_[i]->SetSurfaceColor(0, ui_.BackgroundColor);
            labels_[i]->SetTextColor(OVR::Vector4f(1.0f, 1.0f, 1.0f, 1.0f));
            auto font = labels_[i]->GetFontParms();
            font.Scale = (i >= 2 && i <= 4) ? 1.25f : 0.85f;
            labels_[i]->SetFontParms(font);
            menus_[i] = ui_.GetGuiSys().GetMenu(labels_[i]->GetSurface(0).GetName().c_str());
            if (!menus_[i]) {
                return false;
            }
        }
        return true;
    }

    void AppShutdown(const xrJava* context) override {
        ui_.Shutdown();
        XrApp::AppShutdown(context);
    }

    bool SessionInit() override {
        // The SDK creates StageSpace only when the runtime supports it.
        floorSpace_ = StageSpace != XR_NULL_HANDLE;
        CurrentSpace = floorSpace_ ? StageSpace : LocalSpace;
        sessionId_ = std::chrono::steady_clock::now().time_since_epoch().count();
        spaceEpoch_ = 0;
        __android_log_print(ANDROID_LOG_INFO, "QuestCoordinates", "Reference space: %s",
            floorSpace_ ? "STAGE (floor)" : "LOCAL (not floor calibrated)");
        return true;
    }

    void AppHandleEvent(XrEventDataBaseHeader* event) override {
        if (event->type == XR_TYPE_EVENT_DATA_REFERENCE_SPACE_CHANGE_PENDING) {
            // Consumers must recalibrate after a recenter/reference-space change.
            ++spaceEpoch_;
        }
    }

    void Update(const OVRFW::ovrApplFrameIn& in) override {
        const XrTime time = ToXrTime(in.PredictedDisplayTime);
        const auto head = Locate(HeadSpace, time, true);
        const auto left = Locate(LeftControllerGripSpace, time,
            ActionPoseIsActive(GripPoseAction, LeftHandPath));
        const auto right = Locate(RightControllerGripSpace, time,
            ActionPoseIsActive(GripPoseAction, RightHandPath));

        labels_[1]->SetText(floorSpace_
            ? "FLOOR / STAGE | metres"
            : "LOCAL / NOT FLOOR CALIBRATED");
        SetPositionText(labels_[2], "HEAD", head);
        SetPositionText(labels_[3], "LEFT", left);
        SetPositionText(labels_[4], "RIGHT", right);
        SendThreePoints(in, time, head, left, right);

        // Place the panel 1.5 m in front of the viewer. This changes only its
        // visual placement; the coordinate values remain in the fixed space.
        for (size_t i = 0; i < menus_.size(); ++i) {
            const OVR::Posef offset(OVR::Quatf(0, 0, 0, 1),
                OVR::Vector3f(0.0f, 0.48f - 0.18f * i, -1.5f));
            menus_[i]->SetMenuPose(in.HeadPose * offset);
        }
        ui_.Update(in);

        // Preserve the existing reader protocol and head-relative hand matrices.
        // The HUD uses floor-space coordinates; it does not change legacy data.
        OVRFW::ovrApplFrameIn validFrame = in;
        validFrame.LeftRemoteTracked = Focused && head.valid && left.valid;
        validFrame.RightRemoteTracked = Focused && head.valid && right.valid;
        const OVR::Matrix4f headInverse = OVR::Matrix4f(in.HeadPose).Inverted();
        RemoteState state;
        state.buttonA = GetActionStateBoolean(ButtonAAction).currentState == XR_TRUE;
        state.buttonB = GetActionStateBoolean(ButtonBAction).currentState == XR_TRUE;
        state.buttonX = GetActionStateBoolean(ButtonXAction).currentState == XR_TRUE;
        state.buttonY = GetActionStateBoolean(ButtonYAction).currentState == XR_TRUE;
        state.triggerTouchL_ = GetActionStateBoolean(TriggerTouchAction, LeftHandPath).currentState == XR_TRUE;
        state.triggerTouchR_ = GetActionStateBoolean(TriggerTouchAction, RightHandPath).currentState == XR_TRUE;
        if (validFrame.LeftRemoteTracked) {
            state.LeftRemoteTransform = headInverse * OVR::Matrix4f(in.LeftRemotePose);
        }
        if (validFrame.RightRemoteTracked) {
            state.RightRemoteTransform = headInverse * OVR::Matrix4f(in.RightRemotePose);
        }
        const auto data = remotes_to_string(state, validFrame);
        __android_log_print(ANDROID_LOG_INFO, "wE9ryARX", "%s", data.c_str());
    }

    void Render(const OVRFW::ovrApplFrameIn& in, OVRFW::ovrRendererOutput& out) override {
        ui_.Render(in, out);
    }

   private:
    struct Position {
        bool valid = false;
        bool tracked = false;
        XrVector3f value{};
        bool orientationValid = false;
        bool orientationTracked = false;
        XrQuaternionf orientation{0, 0, 0, 1};
        bool velocityValid = false;
        XrVector3f velocity{};
    };

    Position Locate(XrSpace space, XrTime time, bool active) {
        Position result;
        if (!active || !Focused || space == XR_NULL_HANDLE) {
            return result;
        }
        XrSpaceVelocity velocity{XR_TYPE_SPACE_VELOCITY};
        XrSpaceLocation location{XR_TYPE_SPACE_LOCATION, &velocity};
        if (XR_FAILED(xrLocateSpace(space, CurrentSpace, time, &location))) {
            return result;
        }
        result.valid = (location.locationFlags & XR_SPACE_LOCATION_POSITION_VALID_BIT) != 0;
        result.tracked = result.valid && (location.locationFlags & XR_SPACE_LOCATION_POSITION_TRACKED_BIT) != 0;
        result.value = location.pose.position;
        result.orientationValid = (location.locationFlags & XR_SPACE_LOCATION_ORIENTATION_VALID_BIT) != 0;
        result.orientationTracked = result.orientationValid &&
            (location.locationFlags & XR_SPACE_LOCATION_ORIENTATION_TRACKED_BIT) != 0;
        result.orientation = location.pose.orientation;
        result.velocityValid = result.valid && (velocity.velocityFlags & XR_SPACE_VELOCITY_LINEAR_VALID_BIT) != 0;
        result.velocity = velocity.linearVelocity;
        return result;
    }

    static void WriteVector(std::ostream& out, const XrVector3f& p) {
        out << '[' << -p.z << ',' << -p.x << ',' << p.y << ']';
    }

    static void WritePoint(std::ostream& out, const Position& p) {
        out << "{\"valid\":" << p.valid << ",\"tracked\":" << p.tracked
            << ",\"position_m\":";
        if (p.valid) { WriteVector(out, p.value); } else { out << "null"; }
        out << ",\"orientation_valid\":" << p.orientationValid
            << ",\"orientation_tracked\":" << p.orientationTracked
            << ",\"orientation_xyzw\":";
        if (p.orientationValid) {
            // Change both world and device bases: R_flu = C R_xr C^-1.
            const OVR::Quatf basis(0.5f, -0.5f, -0.5f, 0.5f);
            const auto q = basis * FromXrQuaternionf(p.orientation) * basis.Inverted();
            out << '[' << q.x << ',' << q.y << ',' << q.z << ',' << q.w << ']';
        } else { out << "null"; }
        out << ",\"linear_velocity_m_s\":";
        if (p.velocityValid) { WriteVector(out, p.velocity); } else { out << "null"; }
        out << '}';
    }

    void SendThreePoints(const OVRFW::ovrApplFrameIn& in, XrTime time,
                         const Position& head, const Position& left, const Position& right) {
        std::ostringstream data;
        data.imbue(std::locale::classic());
        data << std::boolalpha << std::fixed << std::setprecision(6)
             << "{\"version\":1,\"session_id\":" << sessionId_
             << ",\"sequence\":" << in.FrameIndex
             << ",\"sample_time_ns\":" << time
             << ",\"space_epoch\":" << spaceEpoch_
             << ",\"reference_space\":\"" << (floorSpace_ ? "stage" : "local")
             << "\",\"axes\":\"flu\",\"focused\":" << Focused
             << ",\"points\":{\"head\":";
        WritePoint(data, head);
        data << ",\"left\":"; WritePoint(data, left);
        data << ",\"right\":"; WritePoint(data, right);
        data << "}}";
        __android_log_print(ANDROID_LOG_INFO, "QuestThreePoint", "%s", data.str().c_str());
    }

    void SetPositionText(OVRFW::VRMenuObject* label, const char* name, const Position& pos) {
        std::ostringstream text;
        text << name;
        if (!Focused) {
            text << "  PAUSED   X: ---  Y: ---  Z: ---";
        } else if (!pos.valid) {
            text << "  NO TRACKING   X: ---  Y: ---  Z: ---";
        } else {
            // Proper rotation from OpenXR (right, up, back) to (forward, left, up).
            // Stage heading is fixed by the runtime, not the user's current gaze.
            text << (pos.tracked ? "" : " [INFERRED]")
                 << std::fixed << std::setprecision(3)
                 << "  X:" << -pos.value.z
                 << " Y:" << -pos.value.x
                 << " Z:" << pos.value.y;
        }
        label->SetText(text.str().c_str());
    }

    OVRFW::TinyUI ui_;
    std::array<OVRFW::VRMenuObject*, 6> labels_{};
    std::array<OVRFW::VRMenu*, 6> menus_{};
    bool floorSpace_ = false;
    bool touchProSupported_ = false;
    int64_t sessionId_ = 0;
    uint64_t spaceEpoch_ = 0;
};

ENTRY_POINT(OculusTeleopApp)
