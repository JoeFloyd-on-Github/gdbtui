// Shared declarations for the gdbtui demo program.
#ifndef DEMO_COMMON_H
#define DEMO_COMMON_H

#include <atomic>
#include <cstdint>
#include <string>

// Cleared by SIGINT / SIGTERM or when --seconds runs out; every thread watches it.
extern std::atomic<bool> g_running;

// Counters updated by all threads (look at them in the Watch window: g_stats).
struct Stats {
    std::atomic<uint64_t> shm_produced{0};
    std::atomic<uint64_t> shm_consumed{0};
    std::atomic<uint64_t> net_requests{0};
    std::atomic<uint64_t> net_replies{0};
    std::atomic<uint64_t> net_bytes{0};
};
extern Stats g_stats;

extern int counter;          // simple global for data breakpoints
extern char buffer[64];      // global for the Memory window

void log_line(const char *who, const std::string &text);   // thread-safe stdout logging
void sleep_ms(int ms);

#endif
