// One binary 16-bit PPM from a packed RGB U16 buffer, with no SDK in it so a test can build it anywhere.
// Exactly width*height*3 samples are read: the SDK pads each resource past the pixels, and a stream that
// wrote that padding would shift every later frame.
#pragma once
#include <cstdint>
#include <cstdio>
#include <vector>

inline bool write_ppm16(FILE* f, const uint16_t* px, uint32_t w, uint32_t h) {
    if (fprintf(f, "P6\n%u %u\n65535\n", w, h) < 0) return false;
    std::vector<uint8_t> row((size_t)w * 6);
    for (uint32_t y = 0; y < h; y++, px += (size_t)w * 3) {
        for (size_t i = 0; i < (size_t)w * 3; i++) { row[2 * i] = px[i] >> 8; row[2 * i + 1] = px[i] & 255; }
        if (fwrite(row.data(), 1, row.size(), f) != row.size()) return false;
    }
    return true;
}
