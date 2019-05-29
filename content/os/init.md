title: xv6: system initialization
category: operating systems
date: 2019-05-29 18:11

在boot流程结束，控制流进入`main`函数的时候，只有唯一的Bootstrap Processor（BSP）在工作，使用`entrypgdir`作为分页表，可以操作的物理地址空间是[0, 4 MB)。`main`将进行一系列的初始化操作，在它结束的时候，其他所有的Application Processor（AP）都已经启动，关于进程的抽象已经建立，系统的第一个进程`console`也已经在运行中。

暂时来说这是xv6系列的最后一篇了。系列目录：

1. [minimal assembly]({filename}/os/assembly.md)
2. [how system boots]({filename}/os/boot.md)
3. [address space]({filename}/os/address.md)
4. [interrupts]({filename}/os/interrupt.md)
5. [system calls]({filename}/os/syscall.md)
6. [process]({filename}/os/process.md)
7. [context switch]({filename}/os/switch.md)
8. [synchronization]({filename}/os/sync.md)
9. [system initialization]({filename}/os/init.md)

### 4 MB过渡空间

main函数首先将单级分页、分页粒度为4 MB的`entrypgdir`切换到更为精细的支持二级分页、分页粒度为4 kB的`kpgdir`。

```c
int main(void) {
  kinit1(end, P2V(4*1024*1024)); // phys page allocator
  kvmalloc();      // kernel page table
  ...
}
```

之前提过，`entrypgdir`分页大小为4 MB，包含两个有效的PTE，分别将虚拟地址的[0, 4 MB)和[2GB, 2GB + 4 MB)都映射到物理地址的[0, 4 MB)上。这4 MB也是系统目前可以操作的内存空间，其中包括了已经被boot和kernel占用的部分和`end`之后未占用两部分。`kinit1`将未占用部分（物理地址在`V2P(end)`到4 MB之间）切分成大小4 kB的小段，依次加入到`kmem`的`freelist`中。

```c
// kalloc.c
void kinit1(void *vstart, void *vend) {
  initlock(&kmem.lock, "kmem");
  kmem.use_lock = 0;
  freerange(vstart, vend);
}
```

然后`kvmalloc`通过`setupkvm`设置一个包含了kernel地址空间分配的分页表`kpgdir`：

```c
// vm.c
pde_t *kpgdir;  // for use in scheduler()

void kvmalloc(void) {
  kpgdir = setupkvm();
  switchkvm();
}
```

`setupkvm`通过`kalloc`从`freelist`中申请若干大小为4 kB的分页，用来建立一个包括了kernel部分地址映射关系的分页表`kpgdir`。最后通过`switchkvm`将`kpgdir`载入%cr3替换`entrypgdir`：

```c
void switchkvm(void) {
  lcr3(V2P(kpgdir));   // switch to the kernel page table
}
```

`kpgdir`可以覆盖[0， 4 GB)的虚拟地址空间，但由于它使用了二级分页，本身只占用两个4 KB的分页（一页PDT，一页覆盖虚拟地址[2 GB, 2 GB + 4 MB)的空间）。注意此时由于`freelist`的限制，系统可操作的空间依旧在4 MB之下。

### 初始化硬件设备

至此为止，只有BSP在工作。`main`接下来的工作是初始化其他设备，包括各种与中断相关的设置、文件系统、硬盘等。其中`seginit`更新了SDT。

```c
int main(void) {
  ...
  mpinit();        // detect other processors
  lapicinit();     // interrupt controller
  seginit();       // segment descriptors
  cprintf("\ncpu%d: starting xv6\n\n", cpunum());
  picinit();       // another interrupt controller
  ioapicinit();    // another interrupt controller
  consoleinit();   // console hardware
  uartinit();      // serial port
  pinit();         // process table
  tvinit();        // trap vectors
  binit();         // buffer cache
  fileinit();      // file table
  ideinit();       // disk
  if(!ismp)
    timerinit();   // uniprocessor timer
  startothers();   // start other processors
  ...
}
```

`startothers`启动其他处理器。此后BSP和AP将进入不同的执行路径。

```c
static void startothers(void) {
  ...
  code = P2V(0x7000);
  memmove(code, _binary_entryother_start, (uint)_binary_entryother_size);
  ...
  for(c = cpus; c < cpus+ncpu; c++){
    if(c == cpus+cpunum())  // We've started already.
      continue;

    stack = kalloc();
    *(void**)(code-4) = stack + KSTACKSIZE;
    *(void**)(code-8) = mpenter;
    *(int**)(code-12) = (void *) V2P(entrypgdir);

    lapicstartap(c->apicid, V2P(code));

    while(c->started == 0)     // wait for cpu to finish mpmain()
      ;
  }
}
```

BSP的路径是执行`startothers`。它首先指定了一个内存地址`code`，`code`处于`bootloader`之下的地址段，此前不被占用。然后将AP的启动程序`entryother`的地址写入`code`。此外`startothers`为每个AP申请一个分页作为启动时所用的栈，设置启动时所用的分页表（AP在启动之初只能操作物理地址，所以此处用`entrypgdir`）。完成准备工作后，BSP通过中断向AP发出启动信号，然后自旋等待直至收到AP启动完成的信号之后，再继续启动下一个AP。

### 其他处理器的启动流程

AP的路径是接收启动信号，根据`startothers`为它设置的环境，执行`entryother`，过程与BSP的启动过程类似：加载`gdtdesc`从real mode切换到protected mode，使用`entrypgdir`作为分页表启动分页，根据`startothers`预设的栈设置%esp，使用这个栈来调用C函数`mpenter`。

```c
static void mpenter(void)
{
  switchkvm();
  seginit();
  lapicinit();
  mpmain();
}
```

`mpenter`通过`switchkvm`将分页表从`entrypgdir`切换到`kpgdir`，`seginit`加载完整的SDT，并最终进入`mpmain`：

```
static void mpmain(void) {
  cprintf("cpu%d: starting\n", cpunum());
  idtinit();       // load idt register
  xchg(&cpu->started, 1); // tell startothers() we're up
  scheduler();     // start running processes
}
```

`mpmain`结束的时候调用`scheduler`进入调度线程。此时进程表中尚没有任何进程，因此在BSP执行`startothers`完毕的时刻，所有AP都在各自的调度线程中循环，等待`RUNNABLE`的进程出现。

### 拓展地址空间

`startothers`启动所有AP之后，初始化已经接近完成，除了一点：至此系统只能操作4 MB的地址空间。BSP接下来调用`kinit2`：

```c
int main(void) {
  ...
  kinit2(P2V(4*1024*1024), P2V(PHYSTOP)); // must come after startothers()
  ...
}
```

`kinit2`将物理地址4 MB之后，`PHYSOTP`之前的内存加入到`freelist`中，并启动`freelist`的锁。

```c
void kinit2(void *vstart, void *vend) {
  freerange(vstart, vend);
  kmem.use_lock = 1;
}
```

AP在启动过程中所能操作的地址空间限制在4 MB之下，所以`kinit2`必须在所有AP都启动之后执行。

### 第一个用户进程

结下来BSP的工作是创建第一个进程，载入用户程序`initcode`。

```c
int main(void) {
  ...
  userinit();      // first user process
  ...
}
```

通常运行用户程序的流程是先从当前进程`fork`出一个新进程，然后在新进程中`exec`运行指定程序。但此时系统中尚没有进程，无从`fork`。所以此处需要一个特殊的函数`userinit`来手动创建进程并载入用户程序：

1. 首先`allocproc`创建进程。具体来说`allocproc`为新进程申请分页作为kernel stack。在kernel stack上为trap frame预留位置，设置从中断返回之后的指令位置为`trapret`，为context留出位置并设置`context->eip`为`forkret`。
2. `setupkvm`创建进程私有的分页表，并且设置kernel部分的地址空间
3. `inituvm`载入用户程序`initcode`，设置user部分的地址空间
4. 手工设置trap frame
5. 将进程状态设置为`RUNNABLE`

```c
void
userinit(void)
{
  struct proc *p;
  extern char _binary_initcode_start[], _binary_initcode_size[];

  acquire(&ptable.lock);

  p = allocproc();
  initproc = p;
  if((p->pgdir = setupkvm()) == 0)
    panic("userinit: out of memory?");
  inituvm(p->pgdir, _binary_initcode_start, (int)_binary_initcode_size);
  p->sz = PGSIZE;
  memset(p->tf, 0, sizeof(*p->tf));
  p->tf->cs = (SEG_UCODE << 3) | DPL_USER;
  p->tf->ds = (SEG_UDATA << 3) | DPL_USER;
  p->tf->es = p->tf->ds;
  p->tf->ss = p->tf->ds;
  p->tf->eflags = FL_IF;
  p->tf->esp = PGSIZE;
  p->tf->eip = 0;  // beginning of initcode.S

  safestrcpy(p->name, "initcode", sizeof(p->name));
  p->cwd = namei("/");

  p->state = RUNNABLE;

  release(&ptable.lock);
}
```

此时AP在各自的`scheduler`中等待，当它们发现这个进程，并且监测到它处于`RUNNABLE`状态时，其中一个AP会通过context switch将这个进程载入处理器执行。

### 第一个用户程序

`initcode`是一段很短的指令，它做的工作是发起中断，执行系统函数`exec`：

```assembly
// initcode.S
# exec(init, argv)
.globl start
start:
  pushl $argv
  pushl $init
  pushl $0  // where caller pc would be
  movl $SYS_exec, %eax
  int $T_SYSCALL

# char init[] = "/init\0";
init:
  .string "/init\0"
...
```

`exec`指向的是一个名为`init`的用户程序。`init`启动`console`，创建最基本的IO：`stdin`、`stdout`和`stderr`。然后进入`fork`新进程，运行shell，等待用户输入命令，待shell结束之后进入新的循环。

```c
// init.c
int main(void) {
  int pid, wpid;

  if(open("console", O_RDWR) < 0){
    mknod("console", 1, 1);
    open("console", O_RDWR);
  }
  dup(0);  // stdout
  dup(0);  // stderr

  for(;;){
    printf(1, "init: starting sh\n");
    pid = fork();
    if(pid < 0){
      printf(1, "init: fork failed\n");
      exit();
    }
    if(pid == 0){
      exec("sh", argv);
      printf(1, "init: exec sh failed\n");
      exit();
    }
    while((wpid=wait()) >= 0 && wpid != pid)
      printf(1, "zombie!\n");
  }
}
```

### 收尾

BSP执行`main`函数的最后一步是启动BSP自己的调度线程：

```c
int main(void) {
  ...
  mpmain();        // finish this processor's setup
}
```

关于调度线程`scheduler`还有两个细节：

- 所有CPU上的`scheduler`使用相同的地址空间`kpgdir`。BSP在`main`函数一开始的时候通过`kvmalloc`创建并载入了`kpgdir`，而AP是在`mpenter`中通过`switchkvm`载入。于是它们可以操作同一个进程表。
- 但各个CPU上的`scheduler`使用各自独立的栈。BSP使用的栈在boot阶段由`entry`指定，AP所使用的栈由BSP在执行`startothers`时设定。

以上是`scheduler`被称为“线程”的原因。

---
#### 参考

1. [UCI course on kernel init](https://www.ics.uci.edu/~aburtsev/143A/lectures/lecture07-kernel-init/lecture07-kernel-init.pdf)
2. [xv6 Book](https://pdos.csail.mit.edu/6.828/2012/xv6/book-rev7.pdf)
