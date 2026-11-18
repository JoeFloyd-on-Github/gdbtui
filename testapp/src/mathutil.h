// Small functions that are handy for stepping into / out of.
#ifndef DEMO_MATHUTIL_H
#define DEMO_MATHUTIL_H

#include <cstdint>
#include <string>

int factorial(int n);                       // recursive: try the Call Stack window
uint64_t fibonacci(int n);
uint32_t checksum(const std::string &text); // FNV-1a

#endif
