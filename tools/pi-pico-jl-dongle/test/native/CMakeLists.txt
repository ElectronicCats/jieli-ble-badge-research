cmake_minimum_required(VERSION 3.13)
project(decoder_native_test C)

set(CMAKE_C_STANDARD 11)
add_executable(test_decoder
    test_decoder.c
    ${CMAKE_CURRENT_LIST_DIR}/../../src/chip-emulator/decoder.c
    unity/src/unity.c
)
target_include_directories(test_decoder PRIVATE
    ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/../../src/chip-emulator
    ${CMAKE_CURRENT_LIST_DIR}/../../common
    unity/src
)
target_compile_definitions(test_decoder PRIVATE NATIVE_TEST)
enable_testing()
add_test(NAME decoder_unit COMMAND test_decoder)
