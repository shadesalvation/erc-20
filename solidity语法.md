# Solidity 语法与 Yul 语法速查手册

整理日期：2026-08-11  
参考基线：Solidity 官方文档 `latest` 页面当前指向 0.8.37-develop，最新稳定页为 0.8.36。生产合约应以具体 `pragma` 锁定编译器版本，并阅读对应版本的 breaking changes。

## 资料来源

- Solidity 官方文档首页：https://docs.soliditylang.org/en/latest/
- 源文件布局：https://docs.soliditylang.org/en/latest/layout-of-source-files.html
- 合约结构：https://docs.soliditylang.org/en/latest/structure-of-a-contract.html
- 类型系统：https://docs.soliditylang.org/en/latest/types.html
- 单位与全局变量：https://docs.soliditylang.org/en/latest/units-and-global-variables.html
- 表达式与控制结构：https://docs.soliditylang.org/en/latest/control-structures.html
- 合约细节：https://docs.soliditylang.org/en/latest/contracts.html
- 内联汇编：https://docs.soliditylang.org/en/latest/assembly.html
- Yul：https://docs.soliditylang.org/en/latest/yul.html
- Solidity 语言文法：https://docs.soliditylang.org/en/latest/grammar.html

## 1. Solidity 源文件结构

Solidity 是面向合约、静态类型、类 C 花括号语法的智能合约语言。一个 `.sol` 源文件可以包含任意数量的 `pragma`、`import`、`using for`、`contract`、`interface`、`library`、`struct`、`enum`、`function`、`error` 和常量定义。

### 1.1 SPDX 许可证标识

```solidity
// SPDX-License-Identifier: MIT
```

通常放在文件第一行。它不是语言运行时逻辑，但编译器和区块浏览器会读取。

### 1.2 Pragma

```solidity
pragma solidity ^0.8.24;
pragma solidity >=0.8.20 <0.9.0;
pragma abicoder v2;
```

`pragma solidity` 只约束编译器版本，不会自动切换编译器。`abicoder v2` 在 0.8.x 已是默认 ABI 编码器。

### 1.3 Import

```solidity
import "./Token.sol";
import {IERC20, IERC20Metadata as Meta} from "./IERC20.sol";
import * as OZ from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
```

### 1.4 文件级定义

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

type Wad is uint256;

struct Position {
    address owner;
    uint256 amount;
}

enum Side {
    Buy,
    Sell
}

error NotOwner(address caller);

uint256 constant BPS = 10_000;

function double(uint256 x) pure returns (uint256) {
    return x * 2;
}

contract Demo {}
interface IDemo {}
library Math {}
```

文件级函数默认是 `internal` 语义，会被内联到调用它们的合约中。

## 2. 注释与文档注释

### 2.1 普通注释

```solidity
// 单行注释

/*
多行注释
*/
```

### 2.2 NatSpec

```solidity
/// @title Vault
/// @notice A tiny deposit vault.
contract Vault {
    /// @notice Deposit ether.
    /// @dev Emits Deposited after accounting.
    /// @param receiver The credited account.
    /// @return shares Minted share amount.
    function deposit(address receiver) external payable returns (uint256 shares) {
        receiver;
        shares = msg.value;
    }
}
```

常用标签：`@title`、`@author`、`@notice`、`@dev`、`@param`、`@return`、`@inheritdoc`、`@custom:name`。

## 3. 合约级语法

### 3.1 Contract

```solidity
contract Bank {
    mapping(address => uint256) public balanceOf;

    function deposit() external payable {
        balanceOf[msg.sender] += msg.value;
    }
}
```

### 3.2 Interface

```solidity
interface IERC20 {
    event Transfer(address indexed from, address indexed to, uint256 value);

    function totalSupply() external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
}
```

接口限制：不能实现函数，不能有构造函数，不能声明状态变量，不能声明 modifier；函数必须是 `external`。

### 3.3 Library

```solidity
library SafeCast {
    function toUint128(uint256 x) internal pure returns (uint128) {
        require(x <= type(uint128).max, "overflow");
        return uint128(x);
    }
}

contract UseCast {
    using SafeCast for uint256;

    function f(uint256 x) external pure returns (uint128) {
        return x.toUint128();
    }
}
```

库不能持有普通可变状态变量，常用于复用逻辑。外部库函数通过 `DELEGATECALL` 在调用者上下文执行。

### 3.4 Abstract Contract

```solidity
abstract contract Base {
    function price() public view virtual returns (uint256);
}

contract Impl is Base {
    function price() public pure override returns (uint256) {
        return 1 ether;
    }
}
```

### 3.5 继承

```solidity
contract A {
    function who() public pure virtual returns (string memory) {
        return "A";
    }
}

contract B is A {
    function who() public pure virtual override returns (string memory) {
        return "B";
    }
}

contract C is A {
    function who() public pure virtual override returns (string memory) {
        return "C";
    }
}

contract D is B, C {
    function who() public pure override(B, C) returns (string memory) {
        return super.who();
    }
}
```

多继承按 C3 线性化处理。覆盖函数需要 `virtual` / `override`。

## 4. 合约成员

### 4.1 状态变量

```solidity
contract StateVars {
    uint256 public count;
    address internal owner;
    bytes32 private salt;

    uint256 public constant FEE_BPS = 30;
    address public immutable deployer;
    uint256 transient lock;

    constructor() {
        deployer = msg.sender;
    }
}
```

状态变量可见性：`public`、`internal`、`private`。`public` 自动生成 getter。  
状态变量修饰：`constant`、`immutable`、`transient`。`transient` 使用瞬态存储，交易结束清空。

### 4.2 Constructor

```solidity
contract Ownable {
    address public owner;

    constructor(address initialOwner) payable {
        owner = initialOwner;
    }
}
```

构造函数只执行一次，不会保存在运行时代码中。每个合约最多一个构造函数。

### 4.3 Function

```solidity
contract Functions {
    function add(uint256 a, uint256 b) public pure returns (uint256 sum) {
        sum = a + b;
    }

    function read() external view returns (uint256) {
        return block.number;
    }

    function pay() external payable {}
}
```

函数可见性：`public`、`external`、`internal`、`private`。  
状态可变性：`pure`、`view`、`payable`，不写则可读写状态但不能收 ETH。  
继承相关：`virtual`、`override`。  
自定义 modifier 可以作为函数修饰器。

### 4.4 Receive 与 Fallback

```solidity
contract Receiver {
    event GotEther(address sender, uint256 value, bytes data);

    receive() external payable {
        emit GotEther(msg.sender, msg.value, "");
    }

    fallback(bytes calldata data) external payable returns (bytes memory) {
        emit GotEther(msg.sender, msg.value, data);
        return data;
    }
}
```

`receive()` 在空 calldata 且收 ETH 时优先触发；`fallback()` 用于无匹配函数或非空 calldata。

### 4.5 Modifier

```solidity
contract Guarded {
    address public owner = msg.sender;
    bool private locked;

    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }

    modifier nonReentrant() {
        require(!locked, "locked");
        locked = true;
        _;
        locked = false;
    }

    function admin() external onlyOwner nonReentrant {}
}
```

`_` 表示被修饰函数体插入位置。modifier 可以有参数。

### 4.6 Event

```solidity
contract Events {
    event Transfer(address indexed from, address indexed to, uint256 value);
    event Note(bytes32 indexed topic, string message) anonymous;

    function emitIt(address to, uint256 value) external {
        emit Transfer(msg.sender, to, value);
    }
}
```

最多 3 个 `indexed` 参数；`anonymous` 事件不把事件签名作为 topic0。

### 4.7 Error

```solidity
error InsufficientBalance(address account, uint256 requested, uint256 available);

contract Errors {
    mapping(address => uint256) public balanceOf;

    function withdraw(uint256 amount) external {
        uint256 bal = balanceOf[msg.sender];
        if (bal < amount) {
            revert InsufficientBalance(msg.sender, amount, bal);
        }
        balanceOf[msg.sender] = bal - amount;
    }
}
```

自定义错误比 revert 字符串更省 gas。

### 4.8 Struct

```solidity
struct Order {
    address maker;
    uint256 amount;
    bool filled;
}

contract Structs {
    Order public last;

    function set(address maker, uint256 amount) external {
        last = Order({maker: maker, amount: amount, filled: false});
    }
}
```

### 4.9 Enum

```solidity
enum Status {
    Pending,
    Filled,
    Cancelled
}

contract Enums {
    Status public status;

    function cancel() external {
        status = Status.Cancelled;
    }
}
```

### 4.10 Using For

```solidity
type Wad is uint256;

function wadAdd(Wad a, Wad b) pure returns (Wad) {
    return Wad.wrap(Wad.unwrap(a) + Wad.unwrap(b));
}

using {wadAdd as +} for Wad global;

contract UseWad {
    function sum(Wad a, Wad b) external pure returns (Wad) {
        return a + b;
    }
}
```

`using L for T;` 给类型 `T` 附加库函数；用户定义值类型还可定义有限集合的运算符重载。

## 5. 类型系统

### 5.1 值类型

```solidity
bool ok = true;
uint256 n = 1;
int128 i = -1;
address a = msg.sender;
address payable p = payable(msg.sender);
bytes32 h = keccak256("x");
```

主要值类型：

- `bool`
- `uint8` 到 `uint256`，步长 8；`uint` 是 `uint256`
- `int8` 到 `int256`，步长 8；`int` 是 `int256`
- `address`、`address payable`
- `bytes1` 到 `bytes32`
- `enum`
- `function` 函数类型
- 用户定义值类型：`type Name is ElementaryType`

### 5.2 Address 成员

```solidity
contract AddressMembers {
    function inspect(address target) external view returns (uint256 bal, uint256 codeSize, bytes32 codeHash) {
        bal = target.balance;
        codeSize = target.code.length;
        codeHash = target.codehash;
    }

    function sendEther(address payable to) external payable {
        (bool ok, ) = to.call{value: msg.value}("");
        require(ok, "send failed");
    }
}
```

常见成员：`.balance`、`.code`、`.codehash`、`transfer`、`send`、`call`、`delegatecall`、`staticcall`。

### 5.3 合约类型

```solidity
interface IPrice {
    function price() external view returns (uint256);
}

contract Caller {
    function get(address feed) external view returns (uint256) {
        return IPrice(feed).price();
    }
}
```

合约类型可显式转换自地址，用于外部调用。

### 5.4 固定长度字节

```solidity
contract FixedBytes {
    bytes4 public selector = bytes4(keccak256("transfer(address,uint256)"));

    function first(bytes32 x) external pure returns (bytes1) {
        return x[0];
    }
}
```

### 5.5 动态字节与字符串

```solidity
contract BytesAndString {
    bytes public raw;
    string public name = "Solidity";

    function pushByte(bytes1 b) external {
        raw.push(b);
    }
}
```

`bytes` 是动态字节数组；`string` 是 UTF-8 字符串，没有直接下标访问。

### 5.6 Array

```solidity
contract Arrays {
    uint256[] public dynamicArray;
    uint256[3] public fixedArray = [uint256(1), 2, 3];

    function useMemory() external pure returns (uint256[] memory arr) {
        arr = new uint256[](2);
        arr[0] = 10;
        arr[1] = 20;
    }

    function mutate() external {
        dynamicArray.push(1);
        dynamicArray.push();
        dynamicArray.pop();
    }
}
```

数组成员：`.length`、`push()`、`push(value)`、`pop()`。动态 memory 数组创建后长度不可变。

### 5.7 Mapping

```solidity
contract Mappings {
    mapping(address account => uint256 balance) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function set(address user, uint256 amount) external {
        balanceOf[user] = amount;
    }
}
```

mapping 只能存在于 `storage`，可作为状态变量、storage 引用、库函数参数等；不能遍历键。

### 5.8 数据位置

```solidity
contract DataLocations {
    uint256[] private store;

    function f(uint256[] calldata input) external returns (uint256[] memory copy) {
        uint256[] storage ref = store;
        ref.push(input[0]);

        copy = new uint256[](input.length);
        copy[0] = input[0];
    }
}
```

引用类型必须考虑数据位置：`storage`、`memory`、`calldata`。状态变量默认在 `storage`。

### 5.9 函数类型

```solidity
contract FunctionTypes {
    function apply(uint256 x, function(uint256) pure returns (uint256) op) public pure returns (uint256) {
        return op(x);
    }

    function inc(uint256 x) public pure returns (uint256) {
        return x + 1;
    }

    function test() external pure returns (uint256) {
        return apply(1, inc);
    }
}
```

函数类型可以是内部或外部函数引用。外部函数值包含地址和 selector。

### 5.10 用户定义值类型

```solidity
type BasisPoints is uint16;

contract UDVT {
    function fee(uint256 amount, BasisPoints bps) external pure returns (uint256) {
        return amount * BasisPoints.unwrap(bps) / 10_000;
    }
}
```

通过 `Type.wrap(x)` 和 `Type.unwrap(x)` 显式转换。

### 5.11 类型转换

```solidity
contract Casts {
    function f(uint256 x, address a) external pure returns (uint128, bytes20) {
        uint128 small = uint128(x);
        bytes20 raw = bytes20(a);
        return (small, raw);
    }
}
```

隐式转换只在无信息损失时允许；窄化、符号变化、地址 payable 转换通常需要显式转换。

## 6. 字面量、单位与全局变量

### 6.1 数值与字符串字面量

```solidity
uint256 a = 1_000_000;
uint256 b = 0xff;
bytes2 c = hex"1234";
string memory s = unicode"你好 Solidity";
```

### 6.2 Ether 与时间单位

```solidity
uint256 oneWei = 1 wei;
uint256 oneGwei = 1 gwei;
uint256 oneEther = 1 ether;

uint256 oneSecond = 1 seconds;
uint256 oneMinute = 1 minutes;
uint256 oneHour = 1 hours;
uint256 oneDay = 1 days;
uint256 oneWeek = 1 weeks;
```

`years` 已移除。时间单位只是数字倍乘，不等于链上真实时间保证。

### 6.3 Block / Msg / Tx

```solidity
contract Globals {
    function info() external payable returns (address, uint256, uint256, bytes4) {
        return (msg.sender, msg.value, block.timestamp, msg.sig);
    }
}
```

常见全局：

- `block.basefee`、`block.blobbasefee`、`block.chainid`、`block.coinbase`、`block.difficulty` / `block.prevrandao`、`block.gaslimit`、`block.number`、`block.timestamp`
- `blobhash(uint index)`
- `blockhash(uint blockNumber)`
- `gasleft()`
- `msg.data`、`msg.sender`、`msg.sig`、`msg.value`
- `tx.gasprice`、`tx.origin`

不要用 `block.timestamp` 或 `blockhash` 作为安全随机数。

### 6.4 ABI 与加密函数

```solidity
contract Builtins {
    function encode(address to, uint256 amount) external pure returns (bytes memory) {
        return abi.encode(to, amount);
    }

    function hash(bytes memory data) external pure returns (bytes32) {
        return keccak256(data);
    }
}
```

常见内建函数：

- ABI：`abi.encode`、`abi.encodePacked`、`abi.encodeWithSelector`、`abi.encodeWithSignature`、`abi.encodeCall`、`abi.decode`
- 哈希：`keccak256`、`sha256`、`ripemd160`
- 签名恢复：`ecrecover`
- 数学辅助：`addmod`、`mulmod`
- 合约销毁：`selfdestruct` 已不推荐

### 6.5 Type Information

```solidity
contract TypeInfo {
    function info() external pure returns (uint256, uint256, bytes32) {
        return (type(uint128).min, type(uint128).max, type(IERC20).interfaceId);
    }
}
```

`type(T).min/max` 用于整数；`type(Contract).name`、`.creationCode`、`.runtimeCode`；`type(Interface).interfaceId`。

## 7. 运算符

### 7.1 算术与位运算

```solidity
contract Operators {
    function f(uint256 x, uint256 y) external pure returns (uint256) {
        uint256 a = x + y;
        uint256 b = x - y;
        uint256 c = x * y;
        uint256 d = x / (y + 1);
        uint256 e = x % (y + 1);
        uint256 p = x ** 2;
        uint256 bits = (x & y) | (x ^ y);
        uint256 shifted = (bits << 1) >> 1;
        return a + b + c + d + e + p + shifted;
    }
}
```

### 7.2 逻辑与比较

```solidity
bool ok = a > b && b != 0 || c == 1;
bool neg = !ok;
```

`&&` 和 `||` 短路求值。

### 7.3 赋值与复合赋值

```solidity
x = 1;
x += 2;
x -= 1;
x *= 3;
x /= 2;
x %= 2;
x <<= 1;
x >>= 1;
x &= 0xff;
x |= 0x10;
x ^= 0x01;
```

### 7.4 自增自减

```solidity
++i;
i++;
--j;
j--;
```

前缀表达式返回修改后的值；后缀表达式返回修改前的值。

### 7.5 三元表达式

```solidity
uint256 fee = amount > 1 ether ? amount / 100 : 0;
```

### 7.6 Delete

```solidity
delete balanceOf[user];
delete dynamicArray;
```

`delete` 把变量重置为类型默认值。

### 7.7 New

```solidity
uint256[] memory arr = new uint256[](3);
Child child = new Child{salt: bytes32(uint256(1)), value: 1 ether}(msg.sender);
```

可创建 memory 动态数组或部署新合约。合约创建可带 `{salt: ...}` 使用 CREATE2。

## 8. 表达式、调用与控制流

### 8.1 If / Else

```solidity
if (amount == 0) {
    revert ZeroAmount();
} else if (amount > limit) {
    revert TooLarge();
} else {
    total += amount;
}
```

条件必须是 `bool`，不能写 `if (1)`。

### 8.2 For / While / Do While

```solidity
for (uint256 i = 0; i < n; ++i) {
    if (i == 3) continue;
    if (i == 9) break;
}

while (n > 0) {
    --n;
}

do {
    ++n;
} while (n < 10);
```

### 8.3 Return

```solidity
function pair() external pure returns (uint256 a, uint256 b) {
    a = 1;
    return (a, 2);
}
```

### 8.4 Try / Catch

```solidity
interface IOracle {
    function price() external view returns (uint256);
}

contract TryCatch {
    function read(IOracle oracle) external view returns (uint256) {
        try oracle.price() returns (uint256 p) {
            return p;
        } catch Error(string memory) {
            return 0;
        } catch Panic(uint256) {
            return 0;
        } catch (bytes memory) {
            return 0;
        }
    }
}
```

`try/catch` 只适用于外部函数调用和合约创建。

### 8.5 Revert / Require / Assert

```solidity
require(msg.sender == owner, "not owner");
if (amount == 0) revert ZeroAmount();
assert(total >= oldTotal);
```

`require` 常用于输入和权限检查；`revert` 可配合自定义错误；`assert` 表示内部不变量，失败触发 Panic。

### 8.6 Checked / Unchecked

```solidity
function dec(uint256 x) external pure returns (uint256) {
    unchecked {
        return x - 1;
    }
}
```

Solidity 0.8 起整数算术默认检查溢出。`unchecked` 可关闭加减乘等溢出检查，但除零检查不能关闭。

### 8.7 函数调用

```solidity
target.transfer(to, amount);
target.transfer({to: to, amount: amount});
feed.info{value: 1 ether, gas: 50_000}();
```

支持位置参数、命名参数、外部调用 options `{value: ..., gas: ...}`。

### 8.8 低级调用

```solidity
(bool ok, bytes memory data) = target.call{value: msg.value}(
    abi.encodeWithSignature("deposit(address)", msg.sender)
);
require(ok, "call failed");
```

低级调用不会自动检查目标函数是否存在，必须检查 `ok` 和返回数据。

## 9. 可见性与状态可变性

```solidity
contract Visibility {
    uint256 public a;
    uint256 internal b;
    uint256 private c;

    function pub() public {}
    function ext() external {}
    function inter() internal {}
    function priv() private {}

    function readOnly() external view returns (uint256) {
        return a;
    }

    function noState(uint256 x) external pure returns (uint256) {
        return x + 1;
    }

    function receiveEth() external payable {}
}
```

- `public`：内部和外部可调用。
- `external`：外部接口调用；内部需 `this.f()`，会产生外部调用。
- `internal`：当前合约和派生合约可见。
- `private`：仅当前合约可见，不代表链上数据私密。

## 10. ABI 相关语法

### 10.1 Selector

```solidity
bytes4 selector = IERC20.transfer.selector;
```

### 10.2 Calldata 编码与解码

```solidity
bytes memory payload = abi.encodeWithSelector(IERC20.transfer.selector, to, amount);
(address decodedTo, uint256 decodedAmount) = abi.decode(data, (address, uint256));
```

### 10.3 多返回值与解构

```solidity
function minMax(uint256 a, uint256 b) pure returns (uint256 min, uint256 max) {
    return a < b ? (a, b) : (b, a);
}

(uint256 lo, uint256 hi) = minMax(1, 2);
(, uint256 onlyHi) = minMax(1, 2);
```

## 11. 存储、内存与布局语法要点

```solidity
contract StorageRefs {
    struct User {
        uint256 balance;
        uint256 nonce;
    }

    mapping(address => User) internal users;

    function bump() external {
        User storage u = users[msg.sender];
        u.nonce++;
    }
}
```

- `storage` 引用指向链上持久存储。
- `memory` 是临时内存，函数执行结束释放。
- `calldata` 是只读调用数据，外部函数参数常用。
- `transient` 状态变量在交易结束清空，适合交易内锁等模式，但组合调用时需谨慎。

## 12. 保留关键字和常见关键字

常用关键字：

```text
abstract after alias anonymous apply as assembly bool break bytes calldata
case catch constant constructor continue contract default delete do else emit
enum error event external fallback false fixed for function global hex if
immutable import indexed interface internal is library mapping memory modifier
new override payable pragma private public pure receive return returns revert
storage string struct super supports switch this throw true try type typedef
ufixed unchecked unicode using view virtual while
```

有些词属于保留字或未来保留字，不一定都是当前可用语法。

## 13. Solidity 语言文法索引

官方 grammar 中的主要产生式可按用途记忆：

- 源文件：`source-unit`、`pragma-directive`、`import-directive`
- 顶层定义：`contract-definition`、`interface-definition`、`library-definition`、`struct-definition`、`enum-definition`、`error-definition`、`function-definition`、`constant-variable-declaration`、`user-defined-value-type-definition`
- 继承：`inheritance-specifier`、`override-specifier`
- 合约成员：`state-variable-declaration`、`constructor-definition`、`function-definition`、`modifier-definition`、`event-definition`、`error-definition`
- 参数与返回：`parameter-list`、`parameter-declaration`、`return-parameters`
- 语句：`block`、`unchecked-block`、`if-statement`、`try-statement`、`while-statement`、`for-statement`、`do-while-statement`、`continue-statement`、`break-statement`、`return-statement`、`emit-statement`、`revert-statement`、`variable-declaration-statement`、`expression-statement`
- 表达式：`expression`、`assignment-operator`、`conditional`、`function-call`、`new-expression`、`tuple-expression`
- 类型：`type-name`、`elementary-type-name`、`function-type-name`、`mapping-type`、`array-type-name`
- 内联汇编：`assembly-statement`、`assembly-flags`

## 14. 内联 Assembly

Solidity 内联汇编使用 Yul 语言：

```solidity
contract InlineAssembly {
    function addAsm(uint256 a, uint256 b) external pure returns (uint256 c) {
        assembly {
            c := add(a, b)
        }
    }

    function loadArray(uint256[] memory arr, uint256 i) external pure returns (uint256 v) {
        assembly {
            v := mload(add(add(arr, 0x20), mul(i, 0x20)))
        }
    }
}
```

内联 assembly 可访问周围 Solidity 局部变量，但不同 assembly 块不共享 Yul 命名空间。使用它会绕过 Solidity 的安全检查，需非常谨慎。

### 14.1 Memory-safe Assembly

```solidity
contract MemorySafe {
    function f(bytes memory data) external pure returns (bytes32 h) {
        assembly ("memory-safe") {
            h := keccak256(add(data, 0x20), mload(data))
        }
    }
}
```

当 assembly 满足内存安全约束时，可标记 `"memory-safe"`，帮助编译器优化。

## 15. Yul 语法

Yul 可独立使用，也可作为 Solidity 内联汇编语言。纯 Yul 本身没有内建类型和函数；EVM dialect 提供 `u256` 以及 EVM opcode 风格的内建函数。

### 15.1 Yul Block

```yul
{
    let x := 1
    let y := 2
    let z := add(x, y)
}
```

Yul 代码块由 `{ ... }` 包围，语句按顺序执行。

### 15.2 变量声明

```yul
{
    let x
    let y := 1
    let a, b := f()

    function f() -> r1, r2 {
        r1 := 10
        r2 := 20
    }
}
```

语法：`let name := expression`。可多返回值赋值。

### 15.3 赋值

```yul
{
    let x := 0
    x := add(x, 1)
}
```

Yul 用 `:=`，不是 Solidity 的 `=`。

### 15.4 If

```yul
{
    let x := 5
    if gt(x, 3) {
        x := add(x, 1)
    }
}
```

Yul 没有 `else`，可用 `switch` 模拟多分支。

### 15.5 Switch

```yul
{
    let x := calldataload(0)
    switch x
    case 0 {
        sstore(0, 100)
    }
    case 1 {
        sstore(0, 200)
    }
    default {
        revert(0, 0)
    }
}
```

### 15.6 For

```yul
{
    let sum := 0
    for { let i := 0 } lt(i, 10) { i := add(i, 1) } {
        sum := add(sum, i)
    }
}
```

Yul 的 `for` 结构是：

```text
for { 初始化块 } 条件表达式 { 后置块 } { 循环体 }
```

### 15.7 Break / Continue

```yul
{
    for { let i := 0 } lt(i, 10) { i := add(i, 1) } {
        if eq(i, 2) { continue }
        if eq(i, 8) { break }
    }
}
```

只能在合法的循环体中使用。

### 15.8 Function

```yul
{
    function square(x) -> y {
        y := mul(x, x)
    }

    let r := square(7)
}
```

Yul 函数参数和返回值都是命名变量。函数内部可用 `leave` 提前返回。

### 15.9 Leave

```yul
{
    function divOrZero(a, b) -> r {
        if iszero(b) {
            r := 0
            leave
        }
        r := div(a, b)
    }
}
```

`leave` 只能在 Yul 函数内使用。

### 15.10 Literals 与 Identifiers

```yul
{
    let a := 123
    let b := 0xff
    let c := "abc"
    let d := true
    let e := false
    let object.name := 1
}
```

Yul 注释和字面量规则大体与 Solidity 相同；Yul 标识符额外允许 `.`。

### 15.11 Yul Object

```yul
object "Simple" {
    code {
        datacopy(0, dataoffset("Runtime"), datasize("Runtime"))
        return(0, datasize("Runtime"))
    }

    object "Runtime" {
        code {
            mstore(0, 42)
            return(0, 32)
        }
    }

    data "Extra" hex"1234"
}
```

Yul object 可包含 `code`、`data` 和子 object。常用内建：`datasize("Name")`、`dataoffset("Name")`、`datacopy(dst, offset, size)`。

### 15.12 完整 Yul 幂函数示例

```yul
{
    function power(base, exponent) -> result {
        result := 1
        for { let i := 0 } lt(i, exponent) { i := add(i, 1) } {
            result := mul(result, base)
        }
    }
}
```

### 15.13 Yul ERC20 风格 selector 分发示例

```yul
object "MiniRuntime" {
    code {
        // selector = first 4 bytes of calldata
        let selector := shr(224, calldataload(0))

        switch selector
        case 0x18160ddd { // totalSupply()
            mstore(0, sload(0))
            return(0, 32)
        }
        case 0x70a08231 { // balanceOf(address)
            let account := shr(96, calldataload(4))
            mstore(0, account)
            mstore(32, 1)
            let slot := keccak256(0, 64)
            mstore(0, sload(slot))
            return(0, 32)
        }
        default {
            revert(0, 0)
        }
    }
}
```

## 16. Yul 形式文法速查

```text
Block                 = '{' Statement* '}'
Statement             = Block
                      | FunctionDefinition
                      | VariableDeclaration
                      | Assignment
                      | If
                      | Expression
                      | Switch
                      | ForLoop
                      | BreakContinue
                      | Leave

FunctionDefinition    = 'function' Identifier '(' TypedIdentifierList? ')'
                        ( '->' TypedIdentifierList )? Block
VariableDeclaration   = 'let' TypedIdentifierList ( ':=' Expression )?
Assignment            = IdentifierList ':=' Expression
Expression            = FunctionCall | Identifier | Literal
If                    = 'if' Expression Block
Switch                = 'switch' Expression ( Case+ Default? | Default )
Case                  = 'case' Literal Block
Default               = 'default' Block
ForLoop               = 'for' Block Expression Block Block
BreakContinue         = 'break' | 'continue'
Leave                 = 'leave'
FunctionCall          = Identifier '(' ( Expression ( ',' Expression )* )? ')'
Identifier            = [a-zA-Z_$] [a-zA-Z_$0-9.]*
IdentifierList        = Identifier ( ',' Identifier)*
TypeName              = Identifier
TypedIdentifierList   = Identifier ( ':' TypeName )?
                        ( ',' Identifier ( ':' TypeName )? )*
Literal               = (NumberLiteral | StringLiteral | TrueLiteral | FalseLiteral)
                        ( ':' TypeName )?
NumberLiteral         = HexNumber | DecimalNumber
StringLiteral         = '"' ([^"\r\n\\] | '\\' .)* '"'
TrueLiteral           = 'true'
FalseLiteral          = 'false'
HexNumber             = '0x' [0-9a-fA-F]+
DecimalNumber         = [0-9]+
```

Yul object 文法：

```text
Object       = 'object' StringLiteral '{' Code ( Object | Data )* '}'
Code         = 'code' Block
Data         = 'data' StringLiteral ( HexLiteral | StringLiteral )
HexLiteral   = 'hex' ('"' ([0-9a-fA-F]{2})* '"' | '\'' ([0-9a-fA-F]{2})* '\'')
StringLiteral = '"' ([^"\r\n\\] | '\\' .)* '"'
```

## 17. EVM Dialect 常用 Yul 内建

### 17.1 算术与比较

```yul
add(x, y)
sub(x, y)
mul(x, y)
div(x, y)
sdiv(x, y)
mod(x, y)
smod(x, y)
exp(x, y)
addmod(x, y, m)
mulmod(x, y, m)
lt(x, y)
gt(x, y)
slt(x, y)
sgt(x, y)
eq(x, y)
iszero(x)
```

### 17.2 位与字节

```yul
and(x, y)
or(x, y)
xor(x, y)
not(x)
byte(n, x)
shl(bits, x)
shr(bits, x)
sar(bits, x)
signextend(i, x)
```

### 17.3 哈希、内存、存储

```yul
keccak256(offset, size)
mload(offset)
mstore(offset, value)
mstore8(offset, value)
sload(slot)
sstore(slot, value)
tload(slot)
tstore(slot, value)
msize()
```

### 17.4 Calldata 与 returndata

```yul
calldataload(offset)
calldatasize()
calldatacopy(dst, offset, size)
returndatasize()
returndatacopy(dst, offset, size)
```

### 17.5 合约与环境

```yul
address()
balance(a)
selfbalance()
caller()
callvalue()
codesize()
codecopy(dst, offset, size)
extcodesize(a)
extcodecopy(a, dst, offset, size)
extcodehash(a)
gas()
gasprice()
origin()
coinbase()
timestamp()
number()
difficulty()
prevrandao()
gaslimit()
chainid()
basefee()
blobbasefee()
blobhash(index)
```

### 17.6 调用、创建、返回与终止

```yul
create(value, offset, size)
create2(value, offset, size, salt)
call(gas, address, value, inOffset, inSize, outOffset, outSize)
callcode(gas, address, value, inOffset, inSize, outOffset, outSize)
delegatecall(gas, address, inOffset, inSize, outOffset, outSize)
staticcall(gas, address, inOffset, inSize, outOffset, outSize)
return(offset, size)
revert(offset, size)
selfdestruct(address)
invalid()
stop()
log0(offset, size)
log1(offset, size, topic1)
log2(offset, size, topic1, topic2)
log3(offset, size, topic1, topic2, topic3)
log4(offset, size, topic1, topic2, topic3, topic4)
```

## 18. 常见完整 Solidity 示例

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

error NotOwner();
error ZeroAmount();

contract MiniVault {
    address public immutable owner;
    mapping(address account => uint256 balance) public balanceOf;
    uint256 public totalDeposits;
    bool private locked;

    event Deposited(address indexed account, uint256 amount);
    event Withdrawn(address indexed account, uint256 amount);

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    modifier nonReentrant() {
        require(!locked, "locked");
        locked = true;
        _;
        locked = false;
    }

    constructor(address initialOwner) payable {
        owner = initialOwner;
        if (msg.value > 0) {
            balanceOf[initialOwner] = msg.value;
            totalDeposits = msg.value;
        }
    }

    receive() external payable {
        _deposit(msg.sender, msg.value);
    }

    function depositFor(address account) external payable {
        _deposit(account, msg.value);
    }

    function withdraw(uint256 amount) external nonReentrant {
        if (amount == 0) revert ZeroAmount();

        uint256 bal = balanceOf[msg.sender];
        require(bal >= amount, "insufficient");

        unchecked {
            balanceOf[msg.sender] = bal - amount;
            totalDeposits -= amount;
        }

        (bool ok, ) = payable(msg.sender).call{value: amount}("");
        require(ok, "eth transfer failed");

        emit Withdrawn(msg.sender, amount);
    }

    function sweep(address payable to) external onlyOwner {
        uint256 dust = address(this).balance - totalDeposits;
        if (dust > 0) {
            (bool ok, ) = to.call{value: dust}("");
            require(ok, "sweep failed");
        }
    }

    function _deposit(address account, uint256 amount) internal {
        if (amount == 0) revert ZeroAmount();
        balanceOf[account] += amount;
        totalDeposits += amount;
        emit Deposited(account, amount);
    }
}
```

## 19. 学习建议

- 学语法时先掌握：文件布局、类型、数据位置、函数可见性、错误处理。
- 写生产合约时优先使用成熟库和测试框架，不要为了省一点 gas 过早使用 assembly。
- 看到 `call`、`delegatecall`、`assembly`、`tx.origin`、`block.timestamp` 随机数、复杂继承、瞬态存储时要重点审计。
- 阅读任何旧教程时检查 Solidity 版本。0.4、0.5、0.6、0.7、0.8 的语法和安全默认值差异很大。
