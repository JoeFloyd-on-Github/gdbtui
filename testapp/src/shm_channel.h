// Producer/consumer ring buffer that lives in POSIX shared memory (/dev/shm).
// The mutex and condition variables are PTHREAD_PROCESS_SHARED, so the same
// layout would also work between separate processes.
#ifndef DEMO_SHM_CHANNEL_H
#define DEMO_SHM_CHANNEL_H

#include <pthread.h>
#include <cstdint>
#include <string>

struct Message {
    uint32_t seq;          // per-producer sequence number
    int32_t producer;      // producer thread id (1, 2, ...)
    uint64_t value;        // fibonacci(seq % 40)
    char text[32];         // printable payload, e.g. "p1-msg17"
};

const uint32_t RING_SLOTS = 16;

struct RingHeader {
    pthread_mutex_t lock;
    pthread_cond_t not_empty;
    pthread_cond_t not_full;
    uint32_t head;         // next slot to read
    uint32_t tail;         // next slot to write
    uint32_t count;        // messages waiting
    uint32_t capacity;
    Message slots[RING_SLOTS];
};

class ShmRing {
public:
    ShmRing();
    ~ShmRing();
    bool create(const std::string &name);   // shm_open + ftruncate + mmap
    void destroy();                         // munmap + shm_unlink
    bool push(const Message &m);            // false when shutting down
    bool pop(Message &m);                   // false when shutting down
    void wake_all();
    const std::string &name() const { return name_; }
private:
    std::string name_;
    int fd_;
    RingHeader *hdr_;
};

void shm_producer(ShmRing *ring, int id);   // thread: makes a message every 200 ms
void shm_consumer(ShmRing *ring);           // thread: checks and counts messages

#endif
