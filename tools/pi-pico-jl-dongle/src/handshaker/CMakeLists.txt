add_executable(handshaker
    main.c
    handshake.c
)

target_link_libraries(handshaker
    pico_stdlib
    pico_cyw43_arch_none
    jl_common
)

target_include_directories(handshaker PRIVATE ${CMAKE_CURRENT_LIST_DIR})

pico_enable_stdio_usb(handshaker 0)
pico_enable_stdio_uart(handshaker 0)
pico_add_extra_outputs(handshaker)
