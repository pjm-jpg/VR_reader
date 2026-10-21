cmake_minimum_required(VERSION 3.10.2)

project(oculusteleop)

# --- Portability: Fetch Meta OpenXR SDK via FetchContent ---
include(FetchContent)
FetchContent_Declare(
  meta_openxr_sdk
  GIT_REPOSITORY https://github.com/meta-quest/Meta-OpenXR-SDK.git
  GIT_TAG        v85
  GIT_SHALLOW    TRUE
)
FetchContent_MakeAvailable(meta_openxr_sdk)

# --- Define Paths based on FetchContent download ---
set(META_SDK_SOURCE_DIR ${meta_openxr_sdk_SOURCE_DIR})
set(META_SDK_SAMPLES_DIR ${META_SDK_SOURCE_DIR}/Samples)

# --- Add SDK Components ---
# We include 3rdParty and SampleXrFramework from the fetched SDK.
# These define 'samplexrframework' and its dependencies (ktx, stb, etc).
add_subdirectory(${META_SDK_SAMPLES_DIR}/3rdParty ${CMAKE_BINARY_DIR}/meta_3rdparty)
add_subdirectory(${META_SDK_SAMPLES_DIR}/SampleXrFramework ${CMAKE_BINARY_DIR}/meta_framework)

# Meta SDK v85 assumes Android onResume/onPause precede XR READY/STOPPING.
# Quest Pro can deliver either XR event first (both observed in crash logs).
# Preserve real Activity state and normal xrBeginSession/xrEndSession handling;
# replace only the ordering assertions in a generated copy, leaving SDK intact.
set(XR_APP_SOURCE "${META_SDK_SAMPLES_DIR}/SampleXrFramework/Src/XrApp.cpp")
file(READ "${XR_APP_SOURCE}" XR_APP_CONTENTS)
string(REPLACE "assert(Resumed == false);"
    "if (Resumed) { ALOGW(\"XR STOPPING arrived before Android onPause\"); }"
    XR_APP_CONTENTS "${XR_APP_CONTENTS}")
string(REPLACE "assert(Resumed);"
    "if (!Resumed) { ALOGW(\"XR READY arrived before Android onResume\"); }"
    XR_APP_CONTENTS "${XR_APP_CONTENTS}")
set(PATCHED_XR_APP "${CMAKE_CURRENT_BINARY_DIR}/XrApp_session_order.cpp")
file(WRITE "${PATCHED_XR_APP}" "${XR_APP_CONTENTS}")
get_target_property(FRAMEWORK_SOURCES samplexrframework SOURCES)
list(REMOVE_ITEM FRAMEWORK_SOURCES "${XR_APP_SOURCE}")
set_property(TARGET samplexrframework PROPERTY SOURCES "${FRAMEWORK_SOURCES}")
target_sources(samplexrframework PRIVATE "${PATCHED_XR_APP}")

# --- Project Source Files ---
set(SRC_FILES
    src/main.cpp
    src/Remotes.h
)

# --- Build Target ---
if(ANDROID)
    add_library(${PROJECT_NAME} MODULE ${SRC_FILES})
    target_compile_features(${PROJECT_NAME} PRIVATE cxx_std_20)

    # Standard Android and SDK libraries
    target_link_libraries(${PROJECT_NAME} PRIVATE
        android
        EGL
        GLESv3
        log
        ktx
        samplexrframework
    )

    # Required for NativeActivity linkage
    set_target_properties(${PROJECT_NAME} PROPERTIES LINK_FLAGS "-u ANativeActivity_onCreate")

    # Ensure NDK glue is found
    target_include_directories(${PROJECT_NAME} PUBLIC ${ANDROID_NDK}/sources/android/native_app_glue)

elseif(WIN32)
    add_executable(${PROJECT_NAME} ${SRC_FILES})
    target_link_libraries(${PROJECT_NAME} PRIVATE samplexrframework)
    add_definitions(-D_USE_MATH_DEFINES)

    # Assets handling for Windows: copy from the fetched SDK sample folder
    add_custom_command(TARGET ${PROJECT_NAME} PRE_BUILD
        COMMAND "${CMAKE_COMMAND}" -E copy_directory
        "${META_SDK_SAMPLES_DIR}/XrSamples/XrControllers/assets"
        "$<TARGET_FILE_DIR:${PROJECT_NAME}>/assets"
        VERBATIM)
endif()

# --- Common Configuration ---
target_include_directories(${PROJECT_NAME} PRIVATE src)
