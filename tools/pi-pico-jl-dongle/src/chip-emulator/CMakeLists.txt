add_executable(chip_emulator
    main.c
    decoder.c
)

target_link_libraries(chip_emulator
    pico_stdlib
    pico_cyw43_arch_none
    jl_common
)

target_include_directories(chip_emulator PRIVATE ${CMAKE_CURRENT_LIST_DIR})

pico_enable_stdio_usb(chip_emulator 1)
pico_enable_stdio_uart(chip_emulator 0)
pico_add_extra_outputs(chip_emulator)
