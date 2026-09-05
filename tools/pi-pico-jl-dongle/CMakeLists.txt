cmake_minimum_required(VERSION 3.13)

include(pico_sdk_import.cmake)

project(pi_pico_jl_dongle C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

pico_sdk_init()

add_library(jl_common INTERFACE)
target_include_directories(jl_common INTERFACE ${CMAKE_CURRENT_LIST_DIR}/common)

add_subdirectory(src/handshaker)
add_subdirectory(src/chip-emulator)
