title: xv6: how kernel boots
category: operating systems
date: 2019-05-26

通常当我们运行某个程序的时候，其实是kernel替我们在运行它，包括启动程序、维护它的状态，以及在结束后清理现场等。这当中的一个问题是，kernel本身作为一个（特殊的）程序是如何启动的？

事实上启动kernel是个比较细致的工作，当中不仅涉及了许多硬件相关的细节，而且前后顺序非常讲究。大体来说包括了以下几个阶段：

- 首先由BIOS载入boot loader
- boot loader启动segment机制实现地址转译，设置内存栈，然后载入并运行elf格式的kernel程序
- kernel实现内存分页，启动中断、多核并行等机制，初始化各个硬件设备
- 创建系统的第一个进程，它将成为此后所有进程的模版
- 在进程中运行shell，为kernel和外界的交互提供入口。

各个阶段层层递进，每个阶段的工作都为下一个阶段准备了必要的条件。

## BIOS

x86启动时，系统的一个CPU成为Bootstrap Processor(BSP)，运行系统初始化指令。其余CPU都成为Application Processor，等待BSP的信号。

BSP首先运行BIOS（Basic I/O System）中的指令。BIOS的工作是初始化硬件、从硬盘boot sector中读取boot loader（负责将kernel载入内存的程序）。xv6的boot loader是一段很短的指令（&lt;512 bytes），存储在第一个disk sector中。BIOS将boot loader载入到起始地址为`0x7c00`的内存中。

BIOS运行结束时，%eip被设为boot loader指令起始的位置`0x7c00`。此后系统就交由boot loader控制。此时状态如下图所示。

![end of bios]({attach}images/boot.001.png)

## Boot Loader

xv6的boot loader分为汇编部分（bootsam.S）和C语言部分（bootmain.c）。汇编部分启动x86的protected mode，在内存中为执行C代码分配栈。C语言部分从硬盘读取kernel指令，并将它加载到内存指定位置。

### 支线剧情：real/protected mode

通常程序中的内存地址是一个“逻辑地址”，包括segment和offset两部分。逻辑地址需要经过转译才能对应到内存单元中的实际位置，即“物理地址”。转译过程的存在使得相同的程序能在不同的硬件上运行。

上古时代x86系统的通用寄存器和segment寄存器都是16位，内存总线是20位（2<sup>20</sup> = 1 MB的地址）。从逻辑地址到物理地址的转译方式是将相应segment寄存器中的地址（segment在物理内存中的起始地址）左移4位（20位，最末4位为0），加上指令中的16位offset。

后来为了支持更大的地址空间，x86引入了一种新的模式protected mode，而将之前的模式称为real mode。为了保持兼容，x86在启动之初都处于real mode中。

protected mode引入了segment descriptor。一个segment descriptor长度为64位，其中包括：

- 32-bit base address
- 20-bit length limit，limit所标示的长度以4 kB为单位（2<sup>12</sup> bit），因此一个segment最长可达4 GB（2<sup>12</sup>&times;2<sup>20</sup> bit）
- some flags: 2-bit descriptor privilege level（DPL）

若干segment descriptor组成一个segment descriptor table（SDT）。系统启动时在内存中划分出一段区间用以保存SDT，并且将它的起始物理地址储存在%gdtr或%ldtr中。

![segment descriptor and selector]({attach}images/boot.002.png)

与此对应，segment寄存器存储的不再是segment的起始地址，而是segment selector，用于指示SDT中的某一行。segment selector长度位16 bit，其中包含：

- 13-bit index: 8192 entries in total
- 1-bit Tabel Indicator(TI):0 for global SDT(%gdtr), 1 for local SDT(%ldtr)
- 2-bit Request Privilege Level(RPL): 0 for the most privileged

在protected mode中从逻辑地址到物理地址的转译方式是：

1. 根据逻辑地址中的segment部分，从相应segment寄存器（%cs、%ss、%ds等）获取segment selector
2. 根据selector中的TI找到相应SDT（%gdtr或者%ldtr），根据selector中的index访问SDT的对应位置，获得segment descriptor
3. 比较RPL和DPL以决定访问是否合法
4. 如果合法（RPL&le;DPL），则用segment descriptor中的base地址加上逻辑地址中的offset部分，获得一个线性地址
5. 如果此线性地址在segment descriptor中的limit所限定的范围内，那么它就是所求的物理地址。

通常用寄存器%cr0中的标志位`$CR0_PE`来标志protected mode是否启用。

### assembly bootstrap

- bootsam.S的第一个动作是暂时关闭中断。此时x86还处在real mode中，只能运行16位指令。

```assembly
.code16                       # x86启动时处于real mode，运行16位指令
.globl start
start:
  cli                         # BIOS运行时启用了interrupt，在这里暂时关闭
```

- 将segment registers（%ds、%es、%ss）置零。然后将SDT（`gdtdesc`）加载到%gdtr中。

```assembly
  xorw    %ax,%ax             # Set %ax to zero
  movw    %ax,%ds             # -> Data Segment
  movw    %ax,%es             # -> Extra Segment
  movw    %ax,%ss             # -> Stack Segment
  
  lgdt    gdtdesc
```

`gdtdesc`是代码中手工定义的一个SDT。共包含null、code、data三个descriptor。其中null segment长度为0，是个无效的映射，任何对应到这个segment的逻辑地址都会抛出异常。而code和data segment的范围是[0, 4 GB)，xv6在内存管理中几乎不使用segment功能，此处`gdtdesc`的设置只是简单地将逻辑地址直接映射到线性区间上。

``` assembly
# Bootstrap GDT
.p2align 2                                # force 4 byte alignment
gdt:
  SEG_NULLASM                             # null segment
  SEG_ASM(STA_X|STA_R, 0x0, 0xffffffff)   # code segment，可读可执行，0～4GB
  SEG_ASM(STA_W, 0x0, 0xffffffff)         # data segment，可写，0～4GB

gdtdesc:
  .word   (gdtdesc - gdt - 1)             # sizeof(gdt) - 1
  .long   gdt                             # address gdt
```

- 设置%cr0，指示protected mode启动。此后开始执行32位指令。

```assembly
  movl    %cr0, %eax
  orl     $CR0_PE, %eax                # $CR0_PE = 1
  movl    %eax, %cr0          

  ljmp    $(SEG_KCODE<<3), $start32    # $SEG_KCODE = 1
```

`ljmp`指令用它的第一个参数设置%cs，用第二个参数设置%eip，并且跳转至这个位置。

- 设置其他的segment寄存器。

```assembly
.code32  # 此时protected mode已经启动，执行32位代码
start32:
  # Set up the protected-mode data segment registers
  movw    $(SEG_KDATA<<3), %ax    # $SEG_KDATA = 2
  movw    %ax, %ds                # -> DS: Data Segment
  movw    %ax, %es                # -> ES: Extra Segment
  movw    %ax, %ss                # -> SS: Stack Segment
  movw    $0, %ax                 # Zero segments not ready for use
  movw    %ax, %fs                # -> FS
  movw    %ax, %gs                # -> GS
```

`$(SEG_KCODE<<3)`和`$(SEG_KDATA<<3)`都是segment selector，分别指向`gdtdesc`中的code和data segment，其中左移的3位将TI和RPL都设置为0。设置完成后%cs指向`gdtdesc`中的code segment，%ds、%ss、%es指向data segment，%fs、%gs指向null segment。

- 为运行C代码设置一个栈。在分页机制启动之前，系统需要手工分配内存以避免冲突。xv6预设boot loader位于内存0x7c00到0x7e00之间，kernel位于内存0x100000之后，0xa0000至0x100000之间有一些硬件设备占用的区域。因此xv6将%esp设置到boot loader开始的位置0x7c00（栈向内存地址低的方向发展，它将占用0x7c00之前的内存区域）。

```assembly
  # Set up the stack pointer and call into C.
  movl    $start, %esp
  call    bootmain
```

它结束的时候调用c语言编写的bootmain函数（系统运行的第一个c函数）。此时系统的状态如下图所示。

![end of assembly bootstrap]({attach}images/boot.003.png)

### 支线剧情： Executable and Linkable Format

the elf format...

### c bootstrap

bootmain.c从硬盘上读取kernel（elf格式的可执行文件），加载到内存制定位置0x100000(定义在kernel.ld中)。xv6预设kernel指令连续地储存在硬盘上boot loader之后的区域内（第二个sector以及之后）。这当然是极其粗暴的简化。现代操作系统需要处理的情况复杂得多，kernel通常存在于某个文件系统中，因此boot loader需要能够操作文件系统，它本身就接近于一个小型的操作系统。

完成后bootmain将控制流交接给kernel的entry。此时系统状态如下图所示。

![end of c bootstrap]({attach}images/boot.004.png)

## Kernel entry

xv6的内存管理主要依赖分页机制。kernel entry部分将启动分页。

### 支线剧情：分页

分页机制主要的想法是将连续的内存空间分成固定大小的页（page），系统以页为单位分配内存。当某个应用程序需要更多内存时，系统从未分配内存中取出一整页供它使用，应用程序可以对这部分内存作精细的操作；当前页写满之后，它可以再向系统申请新的一页。分页机制减少了系统内存分配的次数，可以更方便地实现内存隔离，并且解决了segment机制中难以解决的碎片化问题。

流程上，逻辑地址到物理地址的转译过程是先经segment转译为“线性地址”，再由分页系统最终转译为物理地址。

分页可能将连续线性地址映射到若干离散的物理地址上，于是系统系统需要额外维护一张分页表（page table）来记录这些映射关系。通常在32位系统中，每一个page table entry（PTE）长度为32位。其中包含20位physical page number(PPN)，记录这一页在物理内存中起始的位置，以及若干标志位。标志位中最重要的是`PTE_P`，它标示当前PTE是否有效（是否对应物理内存）。

从虚拟地址到物理地址的转译方式是：

1. 将虚拟地址切分为index和offset两部分
2. 根据index去分页表中查找相应PTE，如果其中`PTE_P`为0，则这个虚拟地址在物理地址中没有对应
3. 如果`PTP_P`为1，则读取PTE中的PPN，PPN+offset即获得物理地址

通常用寄存器%cr0中的标志位`$CR0_PG`来标志分页是否启用， %cr4保存分页的大小，%cr3保存分页表的起始物理地址。

![page table]({attach}images/boot.005.png)

### 第一个分页表

kernel代码中手工设置了xv6运行过程中的第一个分页表`entrypgdir`：

```c
// main.c
pde_t entrypgdir[NPDENTRIES] = {    // NPDENTRIES = 1024
  // Map VA's [0, 4MB) to PA's [0, 4MB)
  [0] = (0) | PTE_P | PTE_W | PTE_PS,
  // Map VA's [KERNBASE, KERNBASE+4MB) to PA's [0, 4MB)
  [KERNBASE>>PDXSHIFT] = (0) | PTE_P | PTE_W | PTE_PS,
};
```

`entrypgdir`选用的分页大小位4 MB，它包括两个有效的entry，分别将虚拟地址[0, 4 MB)和[2GB, 2GB + 4 MB)映射到物理地址[0, 4 MB)。分页机制启动之后，指令中所有的线性地址都需要经过分页表转译，kernel.ld设定了kernel指令中所有线性地址都处于高地址段[2GB, 2GB + 4 MB)。但分页刚启动时寄存器中存储的地址都还处于低地址段[0, 4 MB)中。为此`entrypgdir`设定了两个entry，将高低两段线性地址都映射到相同的物理地址上，以解决二者不一致问题。

### entry

`entry`首先设置分页相关寄存器的状态，启动分页：

```assembly
.globl entry
entry:

  # Turn on page size extension for 4Mbyte pages
  movl    %cr4, %eax
  orl     $(CR4_PSE), %eax  # 支持4 MB分页
  movl    %eax, %cr4
  
  # 设置%cr3，使它指向上述PDT的起始位置
  movl    $(V2P_WO(entrypgdir)), %eax  # entrypgdir是一个VA，需要经过V2P转换
  movl    %eax, %cr3
  
  # 设置%cr0，标示分页机制启动。
  movl    %cr0, %eax
  orl     $(CR0_PG|CR0_WP), %eax  
  movl    %eax, %cr0       # 启动分页
```

另外由于`bootmain`进入`entry`之后函数没有返回，所以此处为之后执行c代码重新分配了一个栈，设定大小为4 kB：

```assembly
.comm stack, KSTACKSIZE    # KSTACKSIZE = 4 kB
  movl $(stack + KSTACKSIZE), %esp
```

最后跳转至kernel的main函数的起始位置。

```assembly
  mov $main, %eax
  jmp *%eax
```

至此进入kernel的`main`函数。此时系统状态如下图所示。注意此时系统实际只能操作4 MB的物理内存。

![end of kernel entry]({attach}images/boot.006.png)

`main`函数最主要的工作是启动系统中的第一个进程。“进程”是操作系统中一个及其关键的抽象，但在具体解释它之前我们需要先了解它的一些组成部分，包括地址空间、user mode和kernel mode、中断机制等。

---
#### 参考

1. [UCI course on system boot](https://www.ics.uci.edu/~aburtsev/143A/lectures/lecture06-system-boot/lecture06-system-boot.pdf)
2. [xv6 Book](https://pdos.csail.mit.edu/6.828/2012/xv6/book-rev7.pdf)
3. [wiki on x86 protected mode](https://en.wikipedia.org/wiki/Protected_mode)
