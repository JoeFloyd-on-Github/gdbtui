#include "shm_channel.h"
#include "common.h"
#include "mathutil.h"

#include <cerrno>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <dirent.h>
#include <cstring>
#include <ctime>
#include <fcntl.h>
#include <map>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

static timespec deadline_after_ms(int ms)
{
    timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    ts.tv_sec += ms / 1000;
    ts.tv_nsec += (ms % 1000) * 1000000L;
    if (ts.tv_nsec >= 1000000000L) {
        ts.tv_sec += 1;
        ts.tv_nsec -= 1000000000L;
    }
    return ts;
}

// Removes /dev/shm/gdbtui_demo_<pid> objects left by demo runs that were killed (e.g. SIGKILL).
static void remove_stale_segments()
{
    DIR *dir = opendir("/dev/shm");
    if (!dir)
        return;
    const char prefix[] = "gdbtui_demo_";
    while (dirent *e = readdir(dir)) {
        if (strncmp(e->d_name, prefix, sizeof prefix - 1) != 0)
            continue;
        int pid = atoi(e->d_name + sizeof prefix - 1);
        if (pid > 0 && kill(pid, 0) != 0 && errno == ESRCH) {
            std::string name = std::string("/") + e->d_name;
            shm_unlink(name.c_str());
        }
    }
    closedir(dir);
}

ShmRing::ShmRing() : fd_(-1), hdr_(nullptr)
{
}

ShmRing::~ShmRing()
{
    destroy();
}

bool ShmRing::create(const std::string &name)
{
    name_ = name;
    remove_stale_segments();
    shm_unlink(name_.c_str());                      // leftover with the same name
    fd_ = shm_open(name_.c_str(), O_CREAT | O_RDWR, 0600);
    if (fd_ < 0) {
        perror("shm_open");
        return false;
    }
    if (ftruncate(fd_, sizeof(RingHeader)) != 0) {
        perror("ftruncate");
        return false;
    }
    void *mem = mmap(nullptr, sizeof(RingHeader), PROT_READ | PROT_WRITE, MAP_SHARED, fd_, 0);
    if (mem == MAP_FAILED) {
        perror("mmap");
        return false;
    }
    hdr_ = static_cast<RingHeader *>(mem);
    memset(hdr_, 0, sizeof(RingHeader));
    hdr_->capacity = RING_SLOTS;

    pthread_mutexattr_t ma;
    pthread_mutexattr_init(&ma);
    pthread_mutexattr_setpshared(&ma, PTHREAD_PROCESS_SHARED);
    pthread_mutex_init(&hdr_->lock, &ma);
    pthread_mutexattr_destroy(&ma);

    pthread_condattr_t ca;
    pthread_condattr_init(&ca);
    pthread_condattr_setpshared(&ca, PTHREAD_PROCESS_SHARED);
    pthread_cond_init(&hdr_->not_empty, &ca);
    pthread_cond_init(&hdr_->not_full, &ca);
    pthread_condattr_destroy(&ca);
    return true;
}

void ShmRing::destroy()
{
    if (hdr_) {
        pthread_cond_destroy(&hdr_->not_empty);
        pthread_cond_destroy(&hdr_->not_full);
        pthread_mutex_destroy(&hdr_->lock);
        munmap(hdr_, sizeof(RingHeader));
        hdr_ = nullptr;
    }
    if (fd_ >= 0) {
        close(fd_);
        fd_ = -1;
        shm_unlink(name_.c_str());
    }
}

bool ShmRing::push(const Message &m)
{
    pthread_mutex_lock(&hdr_->lock);
    while (hdr_->count == hdr_->capacity && g_running) {
        timespec ts = deadline_after_ms(200);
        pthread_cond_timedwait(&hdr_->not_full, &hdr_->lock, &ts);
    }
    if (!g_running) {
        pthread_mutex_unlock(&hdr_->lock);
        return false;
    }
    hdr_->slots[hdr_->tail] = m;
    hdr_->tail = (hdr_->tail + 1) % hdr_->capacity;
    hdr_->count++;
    pthread_cond_signal(&hdr_->not_empty);
    pthread_mutex_unlock(&hdr_->lock);
    return true;
}

bool ShmRing::pop(Message &m)
{
    pthread_mutex_lock(&hdr_->lock);
    while (hdr_->count == 0 && g_running) {
        timespec ts = deadline_after_ms(200);
        pthread_cond_timedwait(&hdr_->not_empty, &hdr_->lock, &ts);
    }
    if (hdr_->count == 0) {
        pthread_mutex_unlock(&hdr_->lock);
        return false;
    }
    m = hdr_->slots[hdr_->head];
    hdr_->head = (hdr_->head + 1) % hdr_->capacity;
    hdr_->count--;
    pthread_cond_signal(&hdr_->not_full);
    pthread_mutex_unlock(&hdr_->lock);
    return true;
}

void ShmRing::wake_all()
{
    if (!hdr_)
        return;
    pthread_mutex_lock(&hdr_->lock);
    pthread_cond_broadcast(&hdr_->not_empty);
    pthread_cond_broadcast(&hdr_->not_full);
    pthread_mutex_unlock(&hdr_->lock);
}

void shm_producer(ShmRing *ring, int id)
{
    uint32_t seq = 0;
    while (g_running) {
        Message m;
        memset(&m, 0, sizeof m);
        m.seq = ++seq;
        m.producer = id;
        m.value = fibonacci(static_cast<int>(seq % 40));
        snprintf(m.text, sizeof m.text, "p%d-msg%u", id, seq);
        if (!ring->push(m))
            break;
        g_stats.shm_produced++;
        sleep_ms(200 + 50 * id);
    }
}

// Checks one message; returns false if it looks corrupted.
static bool process_message(const Message &m, std::map<int, uint32_t> &last_seq)
{
    uint32_t expected = last_seq[m.producer] + 1;
    bool in_order = (m.seq == expected);
    last_seq[m.producer] = m.seq;
    uint32_t sum = checksum(m.text);
    if (m.seq % 10 == 0) {
        char line[128];
        snprintf(line, sizeof line, "got %-10s value=%llu checksum=%08x%s", m.text,
                 static_cast<unsigned long long>(m.value), sum, in_order ? "" : "  (OUT OF ORDER)");
        log_line("shm", line);
    }
    return in_order;
}

void shm_consumer(ShmRing *ring)
{
    std::map<int, uint32_t> last_seq;    // producer id -> last sequence seen
    Message m;
    while (ring->pop(m)) {
        process_message(m, last_seq);
        g_stats.shm_consumed++;
    }
}
