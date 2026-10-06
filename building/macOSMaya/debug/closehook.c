// Debug-only, injected into Maya with DYLD_INSERT_LIBRARIES for M3 test runs:
//  - log who closes the Arras client socket (/tmp/exec-*.ipc)
//  - keep our SIGSEGV/SIGBUS handler installed (Arnold and Maya replace theirs
//    and swallow the stack), print a backtrace, then crash normally.
#include <execinfo.h>
#include <signal.h>
#include <stdlib.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

static int is_arras_socket(int fd, char *path, size_t n)
{
    struct sockaddr_un a; socklen_t len = sizeof(a);
    memset(&a, 0, sizeof(a));
    if (getsockname(fd, (struct sockaddr *)&a, &len) == 0 && a.sun_family == AF_UNIX && strstr(a.sun_path, "exec-")) {
        snprintf(path, n, "local %s", a.sun_path); return 1;
    }
    len = sizeof(a); memset(&a, 0, sizeof(a));
    if (getpeername(fd, (struct sockaddr *)&a, &len) == 0 && a.sun_family == AF_UNIX && strstr(a.sun_path, "exec-")) {
        snprintf(path, n, "peer %s", a.sun_path); return 1;
    }
    return 0;
}

static void report(const char *what, int fd)
{
    char path[200];
    if (!is_arras_socket(fd, path, sizeof(path))) return;
    char name[64] = ""; pthread_getname_np(pthread_self(), name, sizeof(name));
    fprintf(stderr, "\n=== closehook: %s(%d) on Arras socket (%s), thread '%s' %p ===\n",
            what, fd, path, name, (void *)pthread_self());
    void *frames[64]; int n = backtrace(frames, 64);
    backtrace_symbols_fd(frames, n, 2);
    fprintf(stderr, "=== end closehook ===\n");
}

static int my_close(int fd) { report("close", fd); return close(fd); }
static int my_dup2(int a, int b) { report("dup2-target", b); return dup2(a, b); }

static void crash_handler(int sig, siginfo_t *info, void *ctx)
{
    (void)ctx;
    char name[64] = ""; pthread_getname_np(pthread_self(), name, sizeof(name));
    fprintf(stderr, "\n=== closehook: signal %d addr %p thread '%s' ===\n", sig, info ? info->si_addr : 0, name);
    void *frames[128]; int n = backtrace(frames, 128);
    backtrace_symbols_fd(frames, n, 2);
    fprintf(stderr, "=== end closehook crash ===\n");
    signal(sig, SIG_DFL);
    raise(sig);
}

static int my_sigaction(int sig, const struct sigaction *act, struct sigaction *old)
{
    if ((sig == SIGSEGV || sig == SIGBUS) && act) return 0; // keep ours
    return sigaction(sig, act, old);
}
static sig_t my_signal(int sig, sig_t h)
{
    if (sig == SIGSEGV || sig == SIGBUS) return SIG_DFL; // keep ours
    return signal(sig, h);
}

__attribute__((constructor)) static void install(void)
{
    struct sigaction sa; memset(&sa, 0, sizeof(sa));
    sa.sa_sigaction = crash_handler; sa.sa_flags = SA_SIGINFO | SA_ONSTACK;
    sigaction(SIGSEGV, &sa, NULL);
    sigaction(SIGBUS, &sa, NULL);
}

__attribute__((used)) static struct { const void *r; const void *o; } interposers[]
    __attribute__((section("__DATA,__interpose"))) = {
    { (const void *)my_close, (const void *)close },
    { (const void *)my_dup2, (const void *)dup2 },
    { (const void *)my_sigaction, (const void *)sigaction },
    { (const void *)my_signal, (const void *)signal },
};
