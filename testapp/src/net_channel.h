// A tiny line-based request/reply protocol over TCP on 127.0.0.1.
//   PING n      -> PONG n
//   SUM a b     -> OK <a+b>
//   FACT n      -> OK <n!>
//   anything    -> ERR unknown command
#ifndef DEMO_NET_CHANNEL_H
#define DEMO_NET_CHANNEL_H

#include <string>
#include <thread>
#include <vector>

class NetServer {
public:
    NetServer();
    ~NetServer();
    bool start();                 // bind 127.0.0.1 on a free port, listen
    int port() const { return port_; }
    void run();                   // thread: accept loop, one handler thread per client
    void stop();
private:
    int listen_fd_;
    int port_;
    std::vector<std::thread> handlers_;
};

std::string handle_request(const std::string &line);
void net_client(int port, int id);    // thread: sends a request every 300 ms

#endif
