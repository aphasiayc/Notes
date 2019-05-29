title: xv6: synchronization
category: operating systems
date: 2019-05-29 17:34

xv6其实并不支持现代操作系统中的“多线程”。但由于kernel部分的地址空间是各个进程共享的，kernel本身还是需要处理进程之间同步的问题。运行在多处理器系统上，各个进程可能同时操作一个内存地址；即使在单处理器的情况下，在中断机制开启，允许切换进程时，也可能发生多个进程交替操作某个内存地址的情况。为此xv6实现了两种基本的进程间同步的模型：mutual exclusion和producer/comsumer。

## mutual exclusion

### 自旋锁

自旋锁是实现mutual exclusion最简单的一种方法。xv6中定义的`spinlock`包括了一个关键的标志位`locked`：

```c
struct spinlock {
  uint locked;       // Is the lock held?
  ...
};
```

加锁的过程`acquire`：

```c
// spinlock.c
void acquire(struct spinlock *lk) {
  pushcli(); // disable interrupts to avoid deadlock.
  ...
  while(xchg(&lk->locked, 1) != 0)   // The xchg is atomic.
    ;
  ...
}
```

其中`xchg`是一个原子操作，它原子性地将数值写入某个内存位置，并返回此位置原先的值。`acquire`首先暂时禁止中断机制，然后通过`xchg`将`locked`标志位置1。`xchg`如果返回值为1，表示已经有其他进程占用了这个锁，当前进程加锁失败，于是进入不断重试直至成功为止的流程。相反如果`xchg`返回0，则表示当前进程已经成功获得了这个锁，可以愉快的继续执行下面的指令。

在竞争激烈的情况下，获取自旋锁的过程中CPU时间可能都消耗在不断自旋等待之中，效率比较低下。

与`acquire`镜像对称，`release`首先将`locked`标志位置零，然后恢复中断机制。

```c
void release(struct spinlock *lk) {
  ...
  lk->locked = 0;
  popcli();
}
```

xv6在加锁之前首先会通过`pushcli`来禁止中断，相应在解锁之后通过`popcli`重启中断。`pushcli`和`popcli`都是对当前CPU的操作，它们除了调用`cli`和`sti`来操作%eflags中的`FL_IF`标志位之外，还记录了操作的次数。只有当`pushcli`和`popcli`的次数相等时，中断才会被开启。所以当程序中有两个锁处于锁定状态，只释放其中的一个，中断不会被开启，于是也不会发生进程切换。

```c
void pushcli(void) {
  int eflags;

  eflags = readeflags();
  cli();
  if(cpu->ncli == 0)
    cpu->intena = eflags & FL_IF;
  cpu->ncli += 1;
}

void
popcli(void)
{
  if(readeflags()&FL_IF)
    panic("popcli - interruptible");
  if(--cpu->ncli < 0)
    panic("popcli");
  if(cpu->ncli == 0 && cpu->intena)
    sti();
}
```

## producer/comsumer

### sleep和wakeup

除了自旋锁处理的mutual exclusion之外，另一种需要进程间协调的关系是producer/consumer(例如pipe左右的两个进程)。这种关系中，consumer必须等producer完成（部分）工作之后才有事可做，否则处于空转消耗CPU时间的状态。xv6中用`sleep`和`wakeup`两个system call处理这个问题。`sleep`让进程进入休眠状态，让出CPU；`wakeup`通过进程结构体`proc`中的信号量`chan`唤醒休眠的进程。

`sleep`除了要求一个信号量`chan`之外，还要求进程持有一个锁`lk`。进入`sleep`之后，锁定`ptable`，释放其他的锁（与`sched`的要求一致）。然后设置`chan`，将进程的状态设置为`SLEEPING`，然后进入`sched`，让出CPU去运行另一个进程。`ptable`锁将由切换后的进程负责释放。

```c
void sleep(void *chan, struct spinlock *lk) {
  if(proc == 0)
    panic("sleep");
  if(lk == 0)
    panic("sleep without lk");

  if(lk != &ptable.lock){
    acquire(&ptable.lock);
    release(lk);
  }

  proc->chan = chan;
  proc->state = SLEEPING;
  sched();

  proc->chan = 0;
  if(lk != &ptable.lock){
    release(&ptable.lock);
    acquire(lk);
  }
}
```

`walkup`首先锁定`ptable`。然后遍历`ptable`，找到所有匹配信号量`chan`的休眠状态的进程，将它们的状态改为`RUNNABLE`。最后释放`ptable`锁。被唤醒的进程将在未来某个时刻由`scheduler`载入CPU，最终进入`RUNNING`状态。

```c
void wakeup(void *chan) {
  struct proc *p;

  acquire(&ptable.lock);
  
  for(p = ptable.proc; p < &ptable.proc[NPROC]; p++)
    if(p->state == SLEEPING && p->chan == chan)
      p->state = RUNNABLE;

  release(&ptable.lock);
}
```

### 一个栗子：send/receive

`send`和`recv`是关于如何使用`sleep`和`wakeup`的例子。二者之间通过结构体`q`传递一个指针`ptr`。

`send`的流程是：

1. 首先对`q`加锁
2. 然后检查`q`中的`ptr`是否为空，如果为空，则将`p`写入。
3. 如果`ptr`不为空，说明`recv`还没有将之前的值取出，通道堵塞，`send`不能继续工作，所以通过`sleep`让它进入休眠。`sleep`将负责对`q`解锁，以便让`recv`可以工作。
4. `send`将处于`SLEEPING`状态，直至`recv`将信号取走，且将它的状态改为`RUNNABLE`
5. 某个时刻`send`被重新载入CPU执行，它从`sleep`返回，并且返回的过程已经对`q`再次加锁，此时`send`可以将`p`写入
6. `wakeup`所有等待从`q`接收信号的进程
7. 最后对`q`解锁

`recv`的流程几乎与之对称。

```c
struct q {
  struct spinlock lock;
  void* ptr;
};

void* send(struct q *q, void *p) {
  acquire(&q->lock);
  while(q->ptr != 0)
    sleep(q, &q->lock);
  q->ptr = p;
  wakeup(q);
  release(&q->lock);
}

void* recv(struct q *q) {
  void*p;
  
  acquire(&q->lock);
  while((p = q->ptr) == 0)
    sleep(q, &q->lock);
  q->ptr = 0;
  wakeup(q)
  release(&q->lock);
  return p;
}
```

一个细节是`sleep`函数总是处于`while`循环之中，每当从`sleep`返回之后都要重新检查条件是否满足，以避免进程被意外唤醒。

另一个细节是`q`为何需要包括一个锁。这是因为`while`验证条件和调用`sleep`是两个操作，如果在这两步之间发生进程切换的话，可能会发生`wakeup`在`sleep`之前执行的情况，这样当切换回原进程并最终进入休眠之后，将不能再被唤起。

### 一个更完整的栗子：pipe

管道是unix系统的经典设计。它是一种inter-process communication(IPC)机制，通过一个内存中的buffer在两个进程之间传递数据。

xv6中`pipe`的定义：

```c
struct pipe {
  struct spinlock lock;
  char data[PIPESIZE];
  uint nread;     // number of bytes read
  uint nwrite;    // number of bytes written
  ...
};
```

管道连接的两个进程，一个负责写（`pipewrite`），另一个负责读（`piperead`），流程与`send`和`recv`非常接近（`send`和`recv`可以看成是buffer大小为0的管道）。

```c
int pipewrite(struct pipe *p, char *addr, int n) {
  int i;

  acquire(&p->lock);
  for(i = 0; i < n; i++){
    while(p->nwrite == p->nread + PIPESIZE){ 
      ...
      wakeup(&p->nread);
      sleep(&p->nwrite, &p->lock);
    }
    p->data[p->nwrite++ % PIPESIZE] = addr[i];
  }
  wakeup(&p->nread);
  release(&p->lock);
  return n;
}

int piperead(struct pipe *p, char *addr, int n) {
  int i;

  acquire(&p->lock);
  while(p->nread == p->nwrite && p->writeopen){
    ...
    sleep(&p->nread, &p->lock); 
  }
  for(i = 0; i < n; i++){
    if(p->nread == p->nwrite)
      break;
    addr[i] = p->data[p->nread++ % PIPESIZE];
  }
  wakeup(&p->nwrite);
  release(&p->lock);
  return i;
}
```

---
#### 参考
1. [UCI course on synchronization](https://www.ics.uci.edu/~aburtsev/143A/lectures/lecture11-synchronization/lecture11-synchronization.pdf)
2. [xv6 Book](https://pdos.csail.mit.edu/6.828/2012/xv6/book-rev7.pdf)
