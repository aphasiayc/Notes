title: xv6: minimal assembly
category: operating systems
date: 2019-05-25 16:38

和现代操作系统比起来，xv6基本只能算是个复古风格的玩具模型。它仿照unix version 6设计，不涉及现代操作系统中各种复杂的优化。但好处是简单明了，码工精美，十分适合拿来给我等（挑战linux kernel失败的）初级选手作入门教程。

这一系列主要的参考资料是xv6自带说明书[Xv6, a simple Unix-like teaching operating system](https://pdos.csail.mit.edu/6.828/2018/xv6.html)以及更形而上一点的[Operating Systems: Three Easy Pieces](http://pages.cs.wisc.edu/~remzi/OSTEP/)（OSTEP）。

xv6适用于多核x86系统，主要使用ANSI C（以及少量AT&T风格的汇编语言）编码。我在阅读源码过程中最初的障碍来源于对硬件和汇编的无知，所以就从相关基础知识开始。

xv6系列包括：

1. [minimal assembly]({filename}/os/assembly.md)
2. [how system boots]({filename}/os/boot.md)
3. [address space]({filename}/os/address.md)
4. [interrupts]({filename}/os/interrupt.md)
5. [system calls]({filename}/os/syscall.md)
6. [process]({filename}/os/process.md)
7. [context switch]({filename}/os/switch.md)
8. [synchronization]({filename}/os/sync.md)
9. [system initialization]({filename}/os/init.md)

## 寄存器

CPU包含若干组寄存器，xv6涉及到的主要有：

- 8个general purpose registers：%eax、%ebx、%ecx、%edx、%edi、%esi、%ebp、%esp

依据惯例，寄存器名称中的`e`代表extended，标明其长度为32位。它们较低的16位分别可以通过%ax、%bx、%cx、%dx、%di、%si、%bp、%sp访问。修改%ax即修改%eax，反之也对。更近一步，%ax、%bx、%cx、%dx较高的8位可以通过%ah、%bh、%ch、%dh，较低的8位可以通过%al、%bl、%cl、%dl。

其中比较特殊的是%esp（stack pointer，指向栈的最低位置）和%ebp（base pointer，在函数过程中指示frame的起始位置）。

- 1个instruction pointer：%eip

32位。%eip存储program counter，指向当前指令开始的位置。%eip通常不能直接操作，需要通过[control flow instructions](#control-flow-instructions)来控制。

- 4个control registers：%cr0、%cr2、%cr3、%cr4

32位。在xv6中主要用于支持内存分页。%cr0用于存储一系列标识内存状态的flag，%cr3用于存储分页表地址，%cr4用于控制分页大小。

- 6个segment registers：%cs、%ss、%ds、%es、%fs、%gs

16位。包括%cs（code segment），%ss（stack segment），%ds（data segment）等。

- 3个descriptor registers：%gdtr、%ldtr、%idtr

16位。%gdtr、%ldtr用于访问segment descriptor table（SDT），%idtr用于访问interrupt descriptor table（IDT）。

另外还有若干专用的寄存器，例如%eflags用于存储若干CPU状态相关的标志位、%tr用于指示task state segment等。

xv6没有涉及用于浮点数运算、debug和测试等的寄存器。

## 内存

主内存访问速度比寄存器慢10<sup>2</sup>倍。x86系统支持32位地址，可以用于访问4 GB内存空间。

### 静态数据

静态数据可以通过它们在声明时设定的label访问。

```assembly
.data

x:
    .word 42        # 常数
    
array:
    .long 1, 2, 3   # 数组
    
str:
    .string "hello" # 字符串
```

### 访问方式

> addressed by register: (%eax)

> with offset: -4(%eax), memory addressed by %eax-4

> simple arithmetic: (%esi, %eax, 4), memory addressed by %esi+4*%eax

### 长度后缀

当操作所涉及的数据长度存在多种可能的时候，必须用后缀标明。可用的后缀包括`b`（byte）、`w`（word，2 bytes）和`l`（long，4 bytes）。

> mov: movb、movw、movl

> add: addb、addw、addl

## 指令

### data movement instructions

- mov &lt;src&gt; &lt;dst&gt;

`mov`将&lt;src&gt;中的数据复制到&lt;dst&gt;。数据可以在两个寄存器之间、寄存器和内存之间移动，但不能直接在两个内存地址之间移动。

> mov %esi, %eax: move value stored in %esi to %eax

> mov (%esi), %eax: move value stored at the memory position indexed by %esi to %eax

> mov $1, %eax: move value 1 to %eax

> movb $1, (%esi): move one byte value of 1 to the memory position indexed by %esi

- push &lt;val&gt;

`push`指令首先将%esp中的数值减4，然后将&lt;val&gt;复制到%esp指向的内存地址

> push %eax: push value stored in %eax onto stack

> push (%eax): push value stored at the memory position indexed by %eax onto stack

> push $1: push value 1 onto stack

- pop &lt;addr&gt;

`pop`指令将%esp指向的内存中的数据（4 bytes）复制到&lt;addr&gt;中，然后将%esp中的数值加4。&lt;addr&gt;可以是寄存器或内存地址。

> pop %eax: pop value on top of stack to %eax

> pop (%eax): pop value on top of stack to the memory position indexed by %eax

### arithmetic & logic instructions

- add/sub

- inc/dec

- imul

- idiv

- and/or/xor

### <a name="control-flow-instructions"></a>control flow instructions 

- jmp &lt;label&gt;

- cmp & j*condition*

- call &lt;label&gt; 

`call`首先将返回后将下一个指令（即函数返回后将要执行的指令）的地址压栈（称此地址为return address），然后跳转到&lt;label&gt;指向的指令位置。

return address指向%eip+sizeof(call instruction)的位置。在跨segment调用（即所谓far call）的情况下，return address也包含当前指令所属的segment（%cs）。

- ret

`ret`从栈上弹出跳转之前的指令位置，跳转回这个位置。

## 调用规则

C语言的函数调用过程将栈切分成若干frame，每个函数各自维护一个frame。在当前函数的frame中，%ebp指示frame的起始位置（因此%ebp被称为base pointer），而%esp指示栈的底端，即frame的结束位置。

函数调用时，主调和被调函数都需要遵从一定的规则。

### caller rules

在调用之前，主调函数需要做一些准备工作：

1. 被调函数可能会修改寄存器内容，所以在执行之前需要先备份寄存器状态。根据约定，主调函数负责%eax，%ecx，%edx。
2. 将函数参数压栈（x86 64bit有了更多寄存器，当参数不多的时候可以直接通过寄存器传参）。
3. 执行`call`，上文提过，`call`会将返回之后将要运行的指令位置压栈，然后执行一个无条件跳转。

从被调函数返回之后，继续执行当前控制流指令之前，主调函数需要做一些清理工作：

1. 将函数参数从栈上移除。
2. 将之前备份过的%eax、%ecx、%edx中的数据从栈上取出，还原到相应寄存器中。

### callee rules

被调过程在执行自己的指令之前，同样需要做一些准备工作：

1. 开始执行被调函数之前，需要在栈上为它分配一个新的frame。具体的操作是将%ebp中的内容（主调函数对应frame的base）备份到栈上，然后令%ebp指向%esp的位置（当前过程对应的frame的base）。
2. 在栈上为局域变量分配空间
3. 备份寄存器上的数据，按照约定%ebx、%edi、%esi由被调函数负责。

在被调过程返回之前，也有一些收尾工作：

1. 将返回值存储在%eax中。
2. 将之前备份过的%ebx、%edi、%esi中的数据还原。
3. 移除局域变量。这可以通过令%esp指向%ebp（当前过程对应frame的起始位置）实现。
4. 清除当前frame，将之前备份过的主调函数的frame base值从栈上弹出，还原到%ebp中。
5. 执行`ret`指令，上文提过`ret`将从栈上弹出一个指令位置，并无条件跳转到这个位置处。

---
#### 参考：

1. [x86 Assembly Guide](http://flint.cs.yale.edu/cs421/papers/x86-asm/asm.html)
2. [xv6 Book](https://pdos.csail.mit.edu/6.828/2012/xv6/book-rev7.pdf)
