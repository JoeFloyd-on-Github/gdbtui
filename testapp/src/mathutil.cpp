#include "mathutil.h"

int factorial(int n)
{
    if (n <= 1)
        return 1;
    int sub = factorial(n - 1);
    return n * sub;
}

uint64_t fibonacci(int n)
{
    uint64_t a = 0, b = 1;
    for (int i = 0; i < n; i++) {
        uint64_t next = a + b;
        a = b;
        b = next;
    }
    return a;
}

uint32_t checksum(const std::string &text)
{
    uint32_t hash = 2166136261u;
    for (size_t i = 0; i < text.size(); i++) {
        hash ^= static_cast<unsigned char>(text[i]);
        hash *= 16777619u;
    }
    return hash;
}
