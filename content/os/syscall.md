title: xv6: system calls
category: operating systems
date: 2019-05-28 11:17

一种重要的中断机制是应用程序发起system call，要求kernel提供某些服务。xv6中各种涉及资源分配的操作，比如读写硬盘、申请新的内存空间等，都必须在kernel mode下进行，运行在user mode下的应用程序没有足够操作的权限。对此，xv6的办法是由kernel提供一些预设的system call，用户程序通过IDT中的特殊gate，`T_SYSCALL`发起中断。`T_SYSCALL`允许user mode下的调用，在执行`int`指令的过程中进行提权进入kernel mode，然后由kenrel执行指定的操作。

这是xv6系列的第5篇。系列包括：

1. [minimal assembly]({filename}/os/assembly.md)
2. [how system boots]({filename}/os/boot.md)
3. [address space]({filename}/os/address.md)
4. [interrupts]({filename}/os/interrupt.md)
5. [system calls]({filename}/os/syscall.md)
6. [process]({filename}/os/process.md)
7. [context switch]({filename}/os/switch.md)
8. [synchronization]({filename}/os/sync.md)
9. [system initialization]({filename}/os/init.md)

## 执行路径

xv6预设了一个系统函数表`syscalls`，将syscall number对应到具体的kernel中定义函数（函数体在sysproc.c、sysfile.c等文件中）：

```c
// syscall.c
static int (*syscalls[])(void) = {
[SYS_fork]    sys_fork,  // SYS_fork = 1
...
[SYS_close]   sys_close, // SYS_close = 21
};
```

但是这些函数只能在kernel mode中运行（kernel code所处的内存分页`PTE_U`为0），用户程序不能直接调用。

为给用户程序提供入口，xv6提供了一个头文件user.h中，其中声明了一系列system call。用户程序只要`include`这个文件，就可以像调用库函数一样调用这些system call。

```c
// system calls
int fork(void);
int exit(void) __attribute__((noreturn));
int wait(void);
int pipe(int*);
int write(int, void*, int);
int read(int, void*, int);
int close(int);
int kill(int);
int exec(char*, char**);
int open(char*, int);
int mknod(char*, short, short);
int unlink(char*);
int fstat(int fd, struct stat*);
int link(char*, char*);
int mkdir(char*);
int chdir(char*);
int dup(int);
int getpid(void);
char* sbrk(int);
int sleep(int);
int uptime(void);
```

### 一个栗子：fork

以下以system call中的`fork`函数，追踪它的调用路径。它的函数体定义在usys.S中：

```c
// usys.S
#define SYSCALL(name) \
  .globl name; \
  name: \
    movl $SYS_ ## name, %eax; \
    int $T_SYSCALL; \
    ret
    
SYSCALL(fork)
...
```

宏`SYSCALL`将相应的序号保存在%eax中（`fork`对应到序号为`SYS_fork`），然后执行`int`指令进入IDT中序号为`T_SYSCALL`的gate。控制流跳转执行相应的handler，通过`alltraps`建立trap frame，然后调用`trap`函数：

```c
// trap.c
void trap(struct trapframe *tf) {
  if(tf->trapno == T_SYSCALL){
    ...
    proc->tf = tf;  // proc定义在proc.h中，是一个per-CPU变量
    syscall();
    ...
    return;
  }
  ...
}
```

`trap`函数读取trap frame，检查`trapno`，进入`syscall`函数：

```c
// syscall.c
void syscall(void) {
  int num;
  
  num = proc->tf->eax;
  if(num > 0 && num < NELEM(syscalls) && syscalls[num]) {
    proc->tf->eax = syscalls[num]();
  } else {
    ...
    proc->tf->eax = -1;
  }
}
```

`syscall`从trap frame的%eax中读取指定的序号，然后从系统函数表`syscalls`中查找相应的函数。`SYS_fork`对应的是`sys_fork`函数：

```c
int sys_fork(void) {
  return fork();
}
```

至此控制流从用户程序发起的system call`fork`到了kernel中定义的实际负责创建新进程的函数`fork`。

## 如何调用

在应用程序的角度看，调用system call时和通常调用库函数几乎没有区别。以最基础的应用程序shell为例：

```c
// sh.c
#include "user.h"
...

int main(void) {
  ...
  // Read and run input commands.
  while(getcmd(buf, sizeof(buf)) >= 0){
    ...
    if(fork1() == 0)
      runcmd(parsecmd(buf));
    wait();
  }
  exit();
}

int fork1(void) {
  int pid;

  pid = fork();
  if(pid == -1)
    panic("fork");
  return pid;
}
```

shell从输入中得到一个命令之后，会从当前进程`fork`出一个子进程，然后在子进程中执行收到的命令。当前进程执行`wait`直至子进程返回。`fork`、`wait`、`exit`都是system call。

---
#### 参考
1. [UCI course on interrupt](https://www.ics.uci.edu/~aburtsev/143A/lectures/lecture09-interrupts/lecture09-interrupts.pdf)
2. [xv6 Book](https://pdos.csail.mit.edu/6.828/2012/xv6/book-rev7.pdf)
