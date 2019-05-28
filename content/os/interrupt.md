title: xv6: interrupts
category: operating systems
date: 2019-05-28 10:47

顾名思义，中断是处理器暂停当前顺序的控制流，转而执行另外指令的操作。触发中断通常有两种方式：CPU在执行指令过程中发生异常（同步方式），或者接收到了外部硬件发来的信号（异步方式）。按照Intel的分类，同步中断称为exception，而异步中断称为interrupt。通常处理器接受某个中断信号之后，会根据它的具体类型，跳转执行某个特定的interrupt/exception handler。

中断是CPU和外部硬件设备之间，以及多核系统中各个CPU之间通信的一种重要的方式。在xv6中，它为kernel和user程序之间的交互提供了底层机制。

## 中断的分类

中断包括了很多类型，表述上又涉及到许多含义有重叠的名词（比如interrupt、trap，以及exception、fault之类）。所以将中断作个分类考察似乎有助于理解。

### exceptions

exceptions主要有两种来源，一种是程序在执行某个指令时抛出异常，按照处理方式的不同又分为三种：

- fault：通常是遇到了某些可以修复的错误，所以在中断返回之后重新执行当前指令。
- trap：很多情况下是由用于调试，中断返回之后将顺序执行下一条指令
- abort：遇到了严重的、不可修复的问题，系统将终止当前进程

另一种来源是程序通过`int`或者类似的指令主动发起中断。这相当于处理器给自己发出了一个中断信号，所以也被称为“software interrupts”。xv6中的system call就属于这个分类。software interrupts的处理方式与trap类似，返回之后顺序执行下一条指令。

### interrupts

古早时代的单核系统通过一个Programmable Interrupt Controller（PIC）来处理外部外部硬件设备发来的中断信号。外部设备专用于发起中断的输出Interrupt ReQuest (IRQ) Line统一连接到PIC上，经由PIC的I/O与CPU交互。在多核系统中处理中断的硬件要更为复制一些。x86用到了Advanced Programmable Interrupt Controller（APIC），大致的架构如下图所示：

![multiple apic system]({attach}images/interrupt.001.png)

各种外部I/O设备统一接入I/O APIC， I/O APIC作为路由将外部中断信号通过ICC总线转发到各个CPU内置的local APIC上。local APIC之间也可以通过ICC总线互相发送信号。

就是否可以暂时被忽略， interrupts分为maskable和non-maskable两类。绝大多数I/O信号都属于maskable的范围，而non-maskable信号通常来自于硬件异常之类比较特殊的事件。

在CPU一方，它可以选择是否接受硬件发来maskable信号。x86中%eflags寄存器保存了与CPU状态相关的若干标志位，其中包括一个是否接受中断消息的标志位`IF`。指令`cli`将`IF`置零，停止接受中断，与之相反指令`sti`开启中断。

## 基础设施

### interrupt descriptors

x86用一个64位的数据结构interrupt descriptor来指示各个interrupt/exception handler的地址（所以也称gate）。逻辑地址包括segment和offset两部分，与此对应gate中包含了16位segment selector和32位offset。此外还有若干标志位，其中包括2位descriptor privilege level（DPL），4位gate type。

![interrupt descriptors]({attach}images/interrupt.002.png)

gate就进入中断后是否将`IF`标志位置零分为两种：interrupt gate(置零，暂停接受后续中断信号)和trap gate（不置零，允许套叠的中断）。此外还有一种特殊的task gate，gate中的segment selector指向[task state segment](#task-state-segment)。

系统通常预设一个interrupt descriptor table（IDT）。x86用寄存器%idtr保存IDT的起始地址。%idtr为48位，其中32位保存IDT起始地址，16位保存IDT的长度。xv6预设了一个包含256个entry的IDT。

### privilege level

gate中其实包括了两个与权限控制相关的标志位：gate自身的DPL，以及segment selector的DPL。gate的DPL表示的是发起中断所必须的权限，而segment selector的DPL是执行interrupt handler时所需要的权限。xv6中所有interrupt handler都需要在kernel mode下执行，于是segment selector的DPL都为0。但gate的DPL可以不为零，例如xv6中的system call就是程序在user mode中发起中断，要求kernel提供某些服务，因此相应gate的DPL应当设为3。

考虑权限检查，中断的处理流程如下：

1. 根据中断的类型找到IDT中相应的gate
2. 比较gate的DPL和当前CPU的权限CPL（即当前%cs所指示的segment descriptor中的DPL）。由于privilege level取值越小表示权限越高，如果DPL&lt;CPL，则没有权限发起指定中断。在xv6中进程会直接被终止，但一般来说处理这种情况的方法是再发起一个exception，“general protection fault”。
3. 比较gate的DPL和gate中segement selector的DPL已决定是否需要进行权限提升。这个过程需要借助于[task state segment](#task-state-segment)
4. 备份当前%eflags、%cs和%eip的状态
5. 读取gate中的segment selector，据此去GDT中查找对应segment descriptor，获得segment base和segment limit
6. 读取gate中的offset，检查它是否在segment limit限定范围之内
7. 如果需要的化设置%eflags
8. 将gate的segment selector和offset载入%cs和%eip，执行interrupt/exception handler

### <a name="task-state-segment"></a>Task state segment

中断有时会涉及在user mode到kernel mode之间的切换，这个切换过程涉及到将程序使用的栈从user stack切换到kernel stack。为管理kernel stack的位置，xv6使用了一个特殊的数据结构`taskstate`，其中`ss0`和`esp0`字段分别保存了%ss和%esp的状态

```c
// mmu.h
struct taskstate {
  ...
  uint esp0;         // stack pointers and 
  ushort ss0;        // segment selectors after switching to kernel mode
  ...
}
```

与此相应，在GDT中加入特定的一行`SEG_TSS`，用以保存一个指向上述`taskstate`的task segment descriptor。另外有一个专用寄存器task register（%tr），其中保存着指向`SEG_TSS`的segment selector。

每一个进程都维护一个`taskstate`。每当进程将要退出kernel mode，切换到user地址空间的时候，都把当前kernel stack的位置更新到`taskstate`中，并设置GDT和%tr：

```c
// vm.c
void switchuvm(struct proc *p) {
  ...
  cpu->gdt[SEG_TSS] = SEG16(STS_T32A, &cpu->ts, sizeof(cpu->ts)-1, 0);
  cpu->gdt[SEG_TSS].s = 0;
  cpu->ts.ss0 = SEG_KDATA << 3;                    # stack segment
  cpu->ts.esp0 = (uint)proc->kstack + KSTACKSIZE;  # kernel stack 
  ...
  ltr(SEG_TSS << 3);                               # 将segment selector载入%tr中
  ...
}
```

这样当进程下一次进入kernel mode的时候，就可以通过`taskstate`恢复之前的kernel stack。

### instructions

x86提供`int`和`iret`指令，用于发起中断，以及从中断中返回。

- int n

`int`指令接受一个参数n，对应IDT中的第n个gate。

`int`指令的执行流程就是否涉及权限改变分两种情况。当中断不涉及权限改变时（中断之前已经运行在kernel mode中），handler在执行时可以直接使用当前进程的kernel stack：

1. 将%eflags、%cs，%eip（根据中断类型不同可能是当前指令或下一条指令）压栈。%cs和%eip组成了中断返回之后的指令位置，相当与far call指令中的return addresss
2. 将错误码`err`压栈
3. 如果是interrupt gate，将%eflags中IF标志位置零
4. 将interrupt handler对应的segment selector和指令位置分别载入%cs、%eip
5. 完成跳转，开始执行interrupt handler

另一种情况下中断涉及权限提升。xv6中执行system call时就涉及到这个情况：CPL和DPL均为3，但interrupt/exception handler要求DPL为0。这种情况下首先需要权限提升，将进程使用的栈从user stack切换到kernel stack，切换过程如下：

1. 首先将%ss和%esp（user stack的地址）备份在CPU内部寄存器中
2. 读取`taskstate`中的%ss和%esp载入CPU。此后进程不再使用user stack，转而使用kernel stack
3. 将步骤1中备份的%ss、%esp保持到kernel stack上

此时系统进入kernel mode，此后中断的执行过程与不涉及权限改变的情况相同。

- iret

`iret`之于`int`类似`ret`之于`call`，具体的操作是从栈上还原%eip、%cs和%eflags（如果涉及权限改变，还需要还原%ss和%esp），继续中断之前的流程。

### trap frame

与函数调用时使用frame的做法类似，xv6在执行中断时也通过trap frame的来维护寄存器状态。trap frame是kernel stack中的一段区域，它保存着进入中断之前各个寄存器的状态。从中断返回之后，系统需要将trap frame中保存的状态还原到各个寄存器中，以继续中断之前的控制流。

```c
struct trapframe {
  // registers as pushed by pusha
  uint edi;
  uint esi;
  uint ebp;
  uint ebx;
  uint edx;
  uint ecx;
  uint eax;

  // rest of trap frame
  ushort gs;
  ushort fs;
  ushort es;
  ushort ds;

  uint trapno;

  // below here defined by x86 hardware
  uint err;
  uint eip;
  ushort cs;
  uint eflags;

  // below here only when crossing rings, such as from user to kernel
  uint esp;
  ushort ss;
}
```

## 设置IDT

### trap vectors

xv6用一个trap vector数组统一管理interrupt/exception handler的地址。数组`vectors`定义在vector.S中（通过脚本vectors.pl生成）。数组元素`vectors[i]`指向以下一段指令：

```assembly
vectori:
  pushl $0  # errer code, trapframe.err
  pushl $i  # trap no, trapframe.trapno
  jmp alltraps
```

在将错误码`err`和序号`trapno`压栈之后，`vector[i]`跳转到`alltraps`：

```assembly
# trapasm.S
.globl alltraps
alltraps:
  # Build trap frame.
  pushl %ds
  pushl %es
  pushl %fs
  pushl %gs
  pushal    # push all general purpose registers
  
  # Set up data and per-cpu segments.
  movw $(SEG_KDATA<<3), %ax
  movw %ax, %ds
  movw %ax, %es
  movw $(SEG_KCPU<<3), %ax
  movw %ax, %fs
  movw %ax, %gs

  # Call trap(tf), where tf=%esp
  pushl %esp     # trap frame处于当前栈的端，即%esp指向的地址，故此处将%esp压栈作为trap的参数。
  call trap
  addl $4, %esp  # 从栈上弹出参数，`trap`函数结束
```

`alltraps`的工作是：

- 创建trap frame：将除了%cs、%ss之外所有的segment register和所有general purpose registe的内容压栈。%cs和%ss在执行`int`指令的过程中处理。
- 载入data segment和用于维护CPU状态的per-cpu segment
- 调用`trap`函数。之前创建的trap frame是`trap`函数的参数。

与`alltrap`镜像对称，xv6用`trapret`来处理从中断返回的过程：

```assembly
trapret:
  popal
  popl %gs
  popl %fs
  popl %es
  popl %ds
  addl $0x8, %esp  # trapno and errcode
  iret
```

如果追踪各个寄存器状态，进入中断过程中它们的演变过程大致如下：

- 进程通过指令`int`进入中断，如果涉及从user stack到kernel stack的切换，`int`将首先备份user stack对应的%ss和%esp，从`taskstate`中读取kernel stack对应的%ss和%esp。此后使用kernel stack。
- 将当前控制流的%elags、%cs、%eip压栈
- 根据`trapno`从`idt`中查找相应的gate，进行权限验证，如果验证通过，将gate中记录的interrupt handler指令的地址载入%cs和%eip中，并设置%eflags
- 将`err`和`trapno`序号压栈
- 通过`alltraps`建立trap frame，将%ds、%es、%fs、%gs以及所有通用寄器压栈
- 将当前trap frame地址%esp压栈，它将被解读为一个指向`trapframe`的指针被`trap`函数引为参数
- 调用`trap`函数
- 从`trap`函数返回后，从栈上弹出参数
- 通过`trapret`，将执行trap之前通用寄存器和%ds、%es、%fs、%gs的内容从栈上弹出，恢复到各自的位置。从栈上弹出``err`和`trapno`。
- 执行`iret`，恢复%elags、%cs、%eip，如果涉及权限恢复，还需要还原%ss和%esp

### 初始化

xv6创建了一个包含256个entry的IDT`gatedesc`。`tvinit`函数遍历`vectors`，为各个trap vector创建gate，并依次将它们加载到IDT中。

```c
// mmu.h
// - istrap: 1 for a trap (= exception) gate, 0 for an interrupt gate.
//   interrupt gate clears FL_IF, trap gate leaves FL_IF alone
// - sel: Code segment selector for interrupt/trap handler
// - off: Offset in code segment for interrupt/trap handler
// - dpl: Descriptor Privilege Level: the privilege level required to invoke this gate
#define SETGATE(gate, istrap, sel, off, d) {...} 

// trap.c
struct gatedesc idt[256];
extern uint vectors[];  // in vectors.S: array of 256 entry pointers

void tvinit(void) {
  int i;
  for(i = 0; i < 256; i++)
    SETGATE(idt[i], 0, SEG_KCODE<<3, vectors[i], 0);
  SETGATE(idt[T_SYSCALL], 1, SEG_KCODE<<3, vectors[T_SYSCALL], DPL_USER);  // T_SYSCALL = 0x40
  ...
}
```

其中比较特殊的是`idt[T_SYSCALL]`，这是唯一一个允许从user mode中直接发起的中断，对应的是用户程序向kernel发起system call。

`trapno`的定义在traps.h文件中。根据x86的惯例，序号0～19对应一些预设exception信号，外部I/O设备发来的IRQ信号对应到32及之后的序号上。



---
#### 参考
1. [UCI course on interrupt](https://www.ics.uci.edu/~aburtsev/143A/lectures/lecture09-interrupts/lecture09-interrupts.pdf)
2. [xv6 Book](https://pdos.csail.mit.edu/6.828/2012/xv6/book-rev7.pdf)
3. [Understanding the Linux Kernel](http://shop.oreilly.com/product/9780596000028.do)
