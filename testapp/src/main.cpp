// gdbtui demo program: several source files, two groups of threads that run
// until stopped.
//
//   shared memory : 2 producer threads -> ring buffer in /dev/shm -> 1 consumer thread
//   localhost TCP : 2 client threads   -> 127.0.0.1:<free port>   -> server + 1 handler per client
//
// Usage: demo [--seconds N] [--quick] [read] [crash]
//   --seconds N   stop by itself after N seconds (default: run until SIGINT / SIGTERM)
//   --quick       only do the start-up steps, then exit (no threads)
//   read          wait for a line on stdin first (type it in gdbtui's Output tab)
//   crash         dereference a null pointer (exception popup)
// Signals:  SIGUSR1 = print statistics now   SIGHUP = reset statistics
//           SIGINT / SIGTERM = shut down cleanly
#include "common.h"
#include "mathutil.h"
#include "net_channel.h"
#include "shapes.h"
#include "shm_channel.h"

#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <unistd.h>
#include <vector>

std::atomic<bool> g_running(true);
Stats g_stats;
int counter = 0;
char buffer[64] = "hello memory window";

static std::mutex g_log_mutex;
static volatile sig_atomic_t g_want_report = 0;
static volatile sig_atomic_t g_want_reset = 0;

void log_line(const char *who, const std::string &text)
{
    std::lock_guard<std::mutex> lock(g_log_mutex);
    printf("[%-6s] %s\n", who, text.c_str());
    fflush(stdout);
}

void sleep_ms(int ms)
{
    std::this_thread::sleep_for(std::chrono::milliseconds(ms));
}

static void on_signal(int sig)
{
    // only async-signal-safe work here: set flags, the main loop does the rest
    if (sig == SIGUSR1)
        g_want_report = 1;
    else if (sig == SIGHUP)
        g_want_reset = 1;
    else
        g_running = false;
}

struct Options {
    int seconds;
    bool quick;
    bool read;
    bool crash;
};

static Options parse_options(int argc, char **argv)
{
    Options opt = {0, false, false, false};
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--seconds") == 0 && i + 1 < argc)
            opt.seconds = atoi(argv[++i]);
        else if (strcmp(argv[i], "--quick") == 0)
            opt.quick = true;
        else if (strcmp(argv[i], "read") == 0)
            opt.read = true;
        else if (strcmp(argv[i], "crash") == 0)
            opt.crash = true;
    }
    return opt;
}

// The original single-file demo steps: good for first stepping / Locals tests.
static void warm_up()
{
    std::vector<int> numbers = {1, 2, 3, 4, 5};
    std::map<std::string, int> ages = {{"alice", 30}, {"bob", 25}};
    Point p = {3, 4};
    Rect r({0, 0}, {10, 20});
    Circle c({5, 5}, 2.5);
    Shape *s = &r;
    int total = 0;

    for (size_t i = 0; i < numbers.size(); i++) {
        total += numbers[i];
    }
    printf("total=%d area=%.1f circle=%.2f people=%zu\n", total, s->area(), c.area(), ages.size());

    int f = factorial(5);
    printf("factorial(5)=%d fibonacci(20)=%llu\n", f, static_cast<unsigned long long>(fibonacci(20)));

    p.x += total;
    counter += f;
    strcpy(buffer, "changed!");
    printf("p=(%d,%d) buffer=%s counter=%d\n", p.x, p.y, buffer, counter);
}

static void report_stats(const char *why)
{
    char line[200];
    snprintf(line, sizeof line,
             "%s shm produced=%llu consumed=%llu | net requests=%llu replies=%llu bytes=%llu",
             why,
             static_cast<unsigned long long>(g_stats.shm_produced.load()),
             static_cast<unsigned long long>(g_stats.shm_consumed.load()),
             static_cast<unsigned long long>(g_stats.net_requests.load()),
             static_cast<unsigned long long>(g_stats.net_replies.load()),
             static_cast<unsigned long long>(g_stats.net_bytes.load()));
    log_line("main", line);
}

static void reset_stats()
{
    g_stats.shm_produced = 0;
    g_stats.shm_consumed = 0;
    g_stats.net_requests = 0;
    g_stats.net_replies = 0;
    g_stats.net_bytes = 0;
    log_line("main", "statistics reset (SIGHUP)");
}

int main(int argc, char **argv)
{
    Options opt = parse_options(argc, argv);
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    signal(SIGUSR1, on_signal);
    signal(SIGHUP, on_signal);

    printf("demo starting, pid=%d argc=%d\n", static_cast<int>(getpid()), argc);
    warm_up();

    if (opt.crash) {
        int *bad = nullptr;
        *bad = 42;
    }
    if (opt.read) {
        char line[100];
        printf("type something: ");
        fflush(stdout);
        if (fgets(line, sizeof line, stdin))
            printf("you typed: %s", line);
    }
    if (opt.quick)
        return 0;

    // ---- shared-memory pipeline
    ShmRing ring;
    std::string shm_name = "/gdbtui_demo_" + std::to_string(getpid());
    if (!ring.create(shm_name))
        return 1;

    // ---- localhost network pipeline
    NetServer server;
    if (!server.start())
        return 1;

    std::vector<std::thread> threads;
    threads.emplace_back(shm_consumer, &ring);
    for (int id = 1; id <= 2; id++)
        threads.emplace_back(shm_producer, &ring, id);
    threads.emplace_back(&NetServer::run, &server);
    for (int id = 1; id <= 2; id++)
        threads.emplace_back(net_client, server.port(), id);

    log_line("main", "running: shared memory " + shm_name + ", server 127.0.0.1:" +
                     std::to_string(server.port()) + "  (SIGUSR1 = stats, SIGHUP = reset, Ctrl+C = stop)");

    auto started = std::chrono::steady_clock::now();
    int tick = 0;
    while (g_running) {
        sleep_ms(100);
        if (g_want_report) {
            g_want_report = 0;
            report_stats("SIGUSR1:");
        }
        if (g_want_reset) {
            g_want_reset = 0;
            reset_stats();
        }
        if (++tick % 50 == 0)
            report_stats("stats:");
        if (opt.seconds > 0) {
            auto elapsed = std::chrono::steady_clock::now() - started;
            if (elapsed >= std::chrono::seconds(opt.seconds))
                g_running = false;
        }
    }

    log_line("main", "shutting down");
    ring.wake_all();
    for (auto &t : threads)
        t.join();
    server.stop();
    ring.destroy();
    report_stats("final:");
    printf("done buffer=%s counter=%d\n", buffer, counter);
    return 0;
}
