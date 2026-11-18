#include "net_channel.h"
#include "common.h"
#include "mathutil.h"

#include <arpa/inet.h>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <netinet/in.h>
#include <poll.h>
#include <sstream>
#include <sys/socket.h>
#include <unistd.h>

static bool send_all(int fd, const std::string &data)
{
    size_t done = 0;
    while (done < data.size()) {
        ssize_t n = send(fd, data.data() + done, data.size() - done, MSG_NOSIGNAL);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            return false;
        }
        done += static_cast<size_t>(n);
    }
    g_stats.net_bytes += data.size();
    return true;
}

// Reads one '\n'-terminated line. Returns 1 = line, 0 = timeout, -1 = closed / error.
static int recv_line(int fd, std::string &pending, std::string &line, int timeout_ms)
{
    for (;;) {
        size_t nl = pending.find('\n');
        if (nl != std::string::npos) {
            line = pending.substr(0, nl);
            pending.erase(0, nl + 1);
            return 1;
        }
        pollfd p;
        p.fd = fd;
        p.events = POLLIN;
        p.revents = 0;
        int r = poll(&p, 1, timeout_ms);
        if (r == 0)
            return 0;
        if (r < 0)
            return errno == EINTR ? 0 : -1;
        char buf[256];
        ssize_t n = recv(fd, buf, sizeof buf, 0);
        if (n <= 0)
            return -1;
        pending.append(buf, static_cast<size_t>(n));
    }
}

std::string handle_request(const std::string &line)
{
    std::istringstream in(line);
    std::string cmd;
    in >> cmd;
    if (cmd == "PING") {
        long n = 0;
        in >> n;
        return "PONG " + std::to_string(n);
    }
    if (cmd == "SUM") {
        long a = 0, b = 0;
        in >> a >> b;
        return "OK " + std::to_string(a + b);
    }
    if (cmd == "FACT") {
        int n = 0;
        in >> n;
        return "OK " + std::to_string(factorial(n));
    }
    return "ERR unknown command";
}

NetServer::NetServer() : listen_fd_(-1), port_(0)
{
}

NetServer::~NetServer()
{
    stop();
}

bool NetServer::start()
{
    listen_fd_ = socket(AF_INET, SOCK_STREAM, 0);
    if (listen_fd_ < 0) {
        perror("socket");
        return false;
    }
    int one = 1;
    setsockopt(listen_fd_, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    sockaddr_in addr;
    memset(&addr, 0, sizeof addr);
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = 0;                                  // let the kernel pick a free port
    if (bind(listen_fd_, reinterpret_cast<sockaddr *>(&addr), sizeof addr) != 0) {
        perror("bind");
        return false;
    }
    if (listen(listen_fd_, 8) != 0) {
        perror("listen");
        return false;
    }
    socklen_t len = sizeof addr;
    getsockname(listen_fd_, reinterpret_cast<sockaddr *>(&addr), &len);
    port_ = ntohs(addr.sin_port);
    return true;
}

static void handle_client(int fd, int client_no)
{
    std::string pending, line;
    char who[16];
    snprintf(who, sizeof who, "srv%d", client_no);
    while (g_running) {
        int r = recv_line(fd, pending, line, 200);
        if (r < 0)
            break;
        if (r == 0)
            continue;
        g_stats.net_requests++;
        std::string reply = handle_request(line);
        if (!send_all(fd, reply + "\n"))
            break;
    }
    close(fd);
    log_line(who, "connection closed");
}

void NetServer::run()
{
    int client_no = 0;
    while (g_running && listen_fd_ >= 0) {
        pollfd p;
        p.fd = listen_fd_;
        p.events = POLLIN;
        p.revents = 0;
        if (poll(&p, 1, 200) <= 0)
            continue;
        int fd = accept(listen_fd_, nullptr, nullptr);
        if (fd < 0)
            continue;
        client_no++;
        log_line("server", "accepted connection " + std::to_string(client_no));
        handlers_.emplace_back(handle_client, fd, client_no);
    }
    for (auto &t : handlers_)
        t.join();
    handlers_.clear();
}

void NetServer::stop()
{
    if (listen_fd_ >= 0) {
        close(listen_fd_);
        listen_fd_ = -1;
    }
}

void net_client(int port, int id)
{
    int fd = -1;
    for (int attempt = 0; attempt < 50 && g_running; attempt++) {
        fd = socket(AF_INET, SOCK_STREAM, 0);
        sockaddr_in addr;
        memset(&addr, 0, sizeof addr);
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        addr.sin_port = htons(static_cast<uint16_t>(port));
        if (connect(fd, reinterpret_cast<sockaddr *>(&addr), sizeof addr) == 0)
            break;
        close(fd);
        fd = -1;
        sleep_ms(100);
    }
    if (fd < 0) {
        log_line("client", "could not connect");
        return;
    }
    char who[16];
    snprintf(who, sizeof who, "cli%d", id);
    std::string pending, reply;
    for (long i = 1; g_running; i++) {
        std::string request;
        switch (i % 3) {
        case 0: request = "PING " + std::to_string(i); break;
        case 1: request = "SUM " + std::to_string(i) + " " + std::to_string(i * id); break;
        default: request = "FACT " + std::to_string(i % 10); break;
        }
        if (!send_all(fd, request + "\n"))
            break;
        int r;
        while ((r = recv_line(fd, pending, reply, 200)) == 0 && g_running) {
        }
        if (r < 0)
            break;
        g_stats.net_replies++;
        if (i % 10 == 0)
            log_line(who, request + " -> " + reply);
        sleep_ms(300 + 40 * id);
    }
    close(fd);
}
