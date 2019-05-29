title: xv6: process
category: operating systems
date: 2019-05-29 10:17

根据`OSTEP`的说法，操作系统最关键的3个要素是virtualization、concurrency和persistence。关于进程的抽象是第一个关键要素virtualization的核心，是第二个关键要素concurrency的基础。进程之间通过时间分片各自独立地占有处理器，通过维护各自的分页表拥有独立的地址空间。

这是xv6系列的第6篇。以下是xv6系列的目录：

1. [minimal assembly]({filename}/os/assembly.md)
2. [how system boots]({filename}/os/boot.md)
3. [address space]({filename}/os/address.md)
4. [interrupts]({filename}/os/interrupt.md)
5. [system calls]({filename}/os/syscall.md)
6. [process]({filename}/os/process.md)
7. [context switch]({filename}/os/switch.md)
8. [synchronization]({filename}/os/sync.md)
9. [system initialization]({filename}/os/init.md)

## 数据结构

### 进程

结构体`proc`定义了xv6中的进程：

```c
// proc.h
struct proc {
  uint sz;                     // Size of process memory (bytes)
  pde_t* pgdir;                // Page table
  char *kstack;                // Bottom of kernel stack for this process
  enum procstate state;        // Process state
  int pid;                     // Process ID
  struct proc *parent;         // Parent process
  struct trapframe *tf;        // Trap frame for current syscall
  struct context *context;     // swtch() here to run process
  void *chan;                  // If non-zero, sleeping on chan
  int killed;                  // If non-zero, have been killed
  struct file *ofile[NOFILE];  // Open files
  struct inode *cwd;           // Current directory
  char name[16];               // Process name (debugging)
};
```

其中包括了若干重要字段：

- `pid`：进程ID
- `pgdir`：分页表，各个进程维护各自的分页表，各自拥有独立的地址空间
- `kstack`： kernel stack的底端
- `state`：当前进程状态(UNUSED, EMBRYO, SLEEPING, RUNNABLE, RUNNING, ZOMBIE)
- `tf`：trap frame。当进程需要发起system call时用于保存进入中断时建立的trap frame
- `context`：进程间切换时用于保存/恢复寄存器状态
- `chan`： 进程间交互的通道

### 进程表

xv6维护一张全局的分页表`ptable`：

```c
// proc.c
struct {
  struct spinlock lock;
  struct proc proc[NPROC];
} ptable;
```

`ptable`维护一个包含`NPROC`个`proc`的数组。最初进程表中所有`proc`都处于`UNUSED`状态（static variables in c are automatically initialized）。`ptable`使用自旋锁来避免各个进程同时操作的问题。

## 进程创建

### allocate

`allocproc`函数的工作：

1. 遍历`ptable`，在其中寻找一个状态为`UNUSED`的进程
2. 如果找到，将它的状态设为`EMBRYO`，并设置它的`pid`（`pid`是一个单增的数）
3. 通过`kalloc`申请一个内存页作为kernel stack
4. 在kernel stack上为`trapframe`分配地址
5. 在`trapframe`之后将`trapret`的地址压栈，这样进程从`forkret`函数返回之后，将进入`trapret`过程。`trapret`将清理`trapframe`及其他相关参数
6. 在kernel stack上为`context`分配地址，设置`context`中的%eip使之指向`forkret`
7. 最后返回指向当前进程的指针

执行`allocproc`之前必须先获得`ptable`锁。

```c
int nextpid = 1;

static struct proc* allocproc(void) {
  struct proc *p;
  char *sp;

  for(p = ptable.proc; p < &ptable.proc[NPROC]; p++)
    if(p->state == UNUSED)
      goto found;
  return 0;

found:
  p->state = EMBRYO;
  p->pid = nextpid++;

  if((p->kstack = kalloc()) == 0){   // Allocate kernel stack.
    p->state = UNUSED;
    return 0;
  }
  sp = p->kstack + KSTACKSIZE;

  sp -= sizeof *p->tf;    // Leave room for trap frame.
  p->tf = (struct trapframe*)sp;

  // Set up new context to start executing at forkret, which returns to trapret.
  sp -= 4;
  *(uint*)sp = (uint)trapret;

  sp -= sizeof *p->context;
  p->context = (struct context*)sp;
  memset(p->context, 0, sizeof *p->context);
  p->context->eip = (uint)forkret;

  return p;
}
```

### fork

unix在创建进程时的经典设计：将创建新进程的工作拆分成`fork`和`exec`两步。

`fork`的工作是从当前进程（父进程）复制出一个新进程（子进程）。父进程的正常工作流程如下：

1. 首先锁定`ptable`
2. 通过`allocproc`创建子进程`np`
3. 复制当前进程的状态到`np`，包括`pgdir`（由`copyuvm`实现），`trapframe`和打开的文件等
4. 将`np`中`trapframe`中的%eax设为0（根据调用规则，%eax中保存函数返回值）
5. 将`np`的状态从`EMBRYO`更新为`RUNNABLE`
6. 解锁并返回子进程`np`的`pid`

```c
int fork(void) {
  int i, pid;
  struct proc *np;

  acquire(&ptable.lock);

  // Allocate process.
  if((np = allocproc()) == 0){
    release(&ptable.lock);
    return -1;
  }

  // Copy process state from p.
  if((np->pgdir = copyuvm(proc->pgdir, proc->sz)) == 0){
    ... // copying user address space failed, clean up
    return -1;
  }
  np->sz = proc->sz;
  np->parent = proc;
  *np->tf = *proc->tf;

  np->tf->eax = 0; // Clear %eax so that fork returns 0 in child.

  for(i = 0; i < NOFILE; i++)
    if(proc->ofile[i])
      np->ofile[i] = filedup(proc->ofile[i]);
  np->cwd = idup(proc->cwd);

  safestrcpy(np->name, proc->name, sizeof(proc->name));
  pid = np->pid;
  np->state = RUNNABLE;

  release(&ptable.lock);

  return pid;
}
```

子进程的轨迹如下：

1. 由于`fork`将它的状态设为`RUNNABLE`，它将处于可执行状态等待某个CPU的调度进程通过context switch将它切入执行状态
2. context swtich之后，系统将从`context->eip`字段中读取待执行指令的地址。由于 `allocproc`在设置子进程kernel stack时令这个位置指向了`forkret`
3. `forkret`函数返回之后，根据调用规则，系统将读取保存在栈上的return address载入%eip中。而`allocproc`将return address设置为`trapret`。于是当子进程进入`trapret`，它将`trapframe`还原到各个寄存器中
4. `trapret`的最后将执行`iret`从中断返回，函数返回值位于`tf->eax`字段中。`fork`将这个位置置零，所以成功的`fork`在子进程中返回值为0。

xv6在`fork`时，子进程完整地复制了父进程的各种状态，包括分页表、文件列表等。但在`fork`之后立即`exec`执行新程序的情况下，子进程的状态又立即会被重置。所以更现代的unix系统（包括linux）使用了copy-on-write的做法，让`fork`前后的两个进程短暂的共享地址空间，而将复制的操作延迟到有一方需要修改状态时再执行。

### exec

由`fork`而来的子进程最初使用从父进程复制而来的分页表，于是它们使用基本相同的地址空间。但是当子进程需要进行与父进程不同的操作时，就需要通过系统函数`exec`从硬盘读取新的指令，并且重新设置地址空间。和所有系统函数一样，`exec`需要在kernel mode下执行，使用kernel stack。

`exec`首先调用`setupkvm`创建新的分页表，并设置kernel部分的地址空间：

```c
int exec(char *path, char **argv) {
  char *s, *last;
  int i, off;
  uint argc, sz, sp, ustack[3+MAXARG+1];
  struct elfhdr elf;
  struct inode *ip;
  struct proghdr ph;
  pde_t *pgdir, *oldpgdir;

  ... // load inode and check elf header
  if((pgdir = setupkvm()) == 0)
    goto bad;
  ...
```

然后从硬盘读取可执行文件（elf格式），通过`allocuvm`分配一部分属于user部分的地址空间，并且由`loaduvm`将可执行代码载入：

```c
  ...
  // Load program into memory.
  sz = 0;
  for(i=0, off=elf.phoff; i<elf.phnum; i++, off+=sizeof(ph)){
    ... // read program header and sanity checks
    if((sz = allocuvm(pgdir, sz, ph.vaddr + ph.memsz)) == 0)
      goto bad;
    if(ph.vaddr % PGSIZE != 0)
      goto bad;
    if(loaduvm(pgdir, (char*)ph.vaddr, ip, ph.off, ph.filesz) < 0)
      goto bad;
  }
  ... // file operations
```

而后设置进程的user stack。`exec`通过`allocuvm`申请两个新的内存页。在虚拟地址空间中，两个新内存页紧跟在可执行代码所占用的地址之后。其中第一个内存页被设置为user mode下不能访问（`clearpteu`），它的作用是在code和stack之间做一个隔断，避免栈溢出时侵入代码部分的地址空间。第二个内存用作user stack，`sp`是一个指向user stack当前位置的指针。

```c
  ...
  sz = PGROUNDUP(sz);
  if((sz = allocuvm(pgdir, sz, sz + 2*PGSIZE)) == 0)
    goto bad;
  clearpteu(pgdir, (char*)(sz - 2*PGSIZE));
  sp = sz;
  ...  
```

然后处理参数。将参数复制到user stack上：

```c
  ...
  // Push argument strings, prepare rest of stack in ustack.
  for(argc = 0; argv[argc]; argc++) {
    if(argc >= MAXARG)
      goto bad;
    sp = (sp - (strlen(argv[argc]) + 1)) & ~3;
    if(copyout(pgdir, sp, argv[argc], strlen(argv[argc]) + 1) < 0)
      goto bad;
    ustack[3+argc] = sp;
  }
  ustack[3+argc] = 0;

  ustack[0] = 0xffffffff;  // fake return PC
  ustack[1] = argc;
  ustack[2] = sp - (argc+1)*4;  // argv pointer

  sp -= (3+argc+1) * 4;
  if(copyout(pgdir, sp, ustack, (3+argc+1)*4) < 0)
    goto bad;
```

最后切换到user mode。这里需要完成以下操作：

1. 由于从中断返回后，进程将要执行新载入程序的main函数，所以修改trap frame中的%eip为`elf.entry`
2. 同时进程将切换用user stack，所以修改trap frame中的%esp为`sp`
3. 然后通过`switchuvm`保存kernel stack的地址到task state中。这样进程此后如果需要发起中断可以找回kernel stack的位置。

```c
  ...
  oldpgdir = proc->pgdir;
  proc->pgdir = pgdir;
  proc->sz = sz;
  proc->tf->eip = elf.entry;  // main
  proc->tf->esp = sp;
  switchuvm(proc);
  freevm(oldpgdir);
  return 0;
}
```

最后，`exec`包含了一段异常处理代码`bad`：

```c
 bad:
  if(pgdir)
    freevm(pgdir);
  ... // clean up inode related
  }
  return -1;
```

当`exec`执行出错时就跳转到这里。`bad`主要是清理现场，包括通过`freevm`清除`exec`中新建的分页表等。

---
### 参考

1. [UCI course on process](https://www.ics.uci.edu/~aburtsev/143A/lectures/lecture08-creating-processes/lecture08-creating-processes.pdf)
2. [xv6 Book](https://pdos.csail.mit.edu/6.828/2012/xv6/book-rev7.pdf)
3. [Operating Systems: Three Easy Pieces](http://pages.cs.wisc.edu/~remzi/OSTEP/)
