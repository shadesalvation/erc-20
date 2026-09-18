# TKM 后半段恢复问题记录

检查范围：`outputs/assembly样本_sseir_by_contract/0x1e0847e537f75e6a983828c7f8ebf5a8108107d6__TKM/solidity_like.txt` 中从 `Function TKM.setMaxs(address)` 到文件末尾。

本记录只整理当前恢复结果中存在的问题，后续可按条逐项修复。

## 1. setMaxs(address)

原始语义：

```solidity
ae = keccak256(msg.sender);
check = o[ae];
require(check);

af = keccak256(_max_);
o[af] = true;
```

当前恢复中存在问题：

```solidity
check = o[af];
o[af] = 1;
```

问题点：

- `check` 的 key 应该是 `ae`，不是后续生成的 `af`。
- 后续写入 `o[af] = 1` 是正确的。
- 该问题表现为同函数内后续 hash slot alias 污染了前面的 `sload` 解析。

## 2. transferFrom(address,address,uint256)

原始 Yul：

```solidity
if gt(amount, currentAllowance) {
    revert(0, 0)
}
```

期望语义：

```solidity
require(amount <= currentAllowance);
```

当前恢复：

```solidity
require(((amount != 0) <= (currentAllowance != 0)));
```

问题点：

- 数值比较被错误布尔化。
- 该表达式会改变语义，例如 `amount = 2`、`currentAllowance = 1` 时会错误放行。
- `currentAllowance = m[from][msg.sender]` 和 `m[from][msg.sender] = ah` 的 mapping 恢复本身是正确的。

## 3. D(address,address,address,uint256)

原始语义：

```solidity
if (am) {
    an = q[to];
    if (!an) revert;
}

if (ap) {
    aq = p[from];
    if (!aq) revert;
}
```

期望恢复：

```solidity
if (am) {
    an = q[to];
    require(an != 0);
}

if (ap) {
    aq = p[from];
    require(aq != 0);
}
```

当前恢复中存在问题：

```solidity
if (am) {
    an = q[to];
}
require((an != 0));

...

require((aq != 0));
```

问题点：

- `require(an != 0)` 应该处于 `am` 控制范围内。
- `require(aq != 0)` 应该处于 `ap` 控制范围内。
- S-SEIR 模型中的 `RequireOverlay.control_path` 已记录类似 `am && iszero(an)` 的条件，但 solidity-like 渲染没有正确保留 guard 作用域。

## 4. setFlag(bool,bool)

当前恢复：

```solidity
check = o[ar];

if (check) {
    t = _flag;
    s = _isfee;
}
```

检查结论：

- 关键语义基本正确。
- `ar = keccak256(0, 32)` 仍是低层展示，但该类中间 hash 当前允许暂时保留。

## 5. E(address)

当前恢复：

```solidity
result = o[as];
```

检查结论：

- 关键语义正确。
- `as = keccak256(0, 32)` 的展示暂时保留即可。

## 6. C(address,address,uint256)

### 6.1 require 条件错误

原始 Yul：

```solidity
if lt(fromBalance, amount) {
    revert(0, 0)
}
```

期望语义：

```solidity
require(fromBalance >= amount);
```

当前恢复：

```solidity
require(((fromBalance != 0) >= (amount != 0)));
```

问题点：

- 与 `transferFrom` 同类，数值比较被布尔化。
- 该问题会改变真实安全语义。

### 6.2 feeAmount 赋值丢失

原始 Yul：

```solidity
let feeAmount := div(mul(amount, 5), 100)
```

期望恢复：

```solidity
feeAmount = (amount * 5) / 100;
```

当前恢复只剩：

```solidity
require(100 != 0);
finalAmount = (amount - feeAmount);
```

问题点：

- 除零 guard 被生成了，但原本的 `feeAmount` 赋值缺失。
- 这会导致后续 `feeAmount` 使用来源不明。

### 6.3 事件重复

当前 `Transfer` 和未知 `Reduce` 事件被拆成多个互斥 condition 下重复 emit。

问题点：

- 语义多数情况下仍可理解，但可读性较差。
- 后续可做相同事件、相同参数、互补路径条件的合并优化。

### 6.4 正确部分

以下 mapping 恢复基本正确：

```solidity
check = o[at];
fromBalance = l[from];
l[from] = newFromBalance;
toBalance = l[to];
isFeeRouter = r[from];
feeToBalance = l[feeToAddr];
l[feeToAddr] = feeToBalance + feeAmount;
l[to] = toBalance + finalAmount;
```

## 7. B(address,address,uint256)

原始语义：

```solidity
require(owner_ != address(0) && spender != address(0));
m[owner_][spender] = amount;
emit Approval(owner_, spender, amount);
```

当前恢复：

```solidity
require(!(or(iszero((owner_ != 0)), iszero((spender != 0)))));
m[owner_][spender] = amount;
emit Approval(owner_, spender, amount);
```

检查结论：

- mapping 写入和事件识别正确。
- require 语义接近正确，但展示不够高级，应后续优化为地址零检查形式。

## 8. setFromRouterFlag(address,bool)

原始语义：

```solidity
hash = keccak256(msg.sender);
check = o[hash];

if (check) {
    p[routerAddr] = _flag;
}
```

当前恢复存在问题：

```solidity
if (check) {
    check = p[routerAddr];
}

if (check) {
    p[routerAddr] = _flag;
}
```

问题点：

- 第一段权限读取应为 `check = o[hash]`。
- 不应被放入 `if (check)` 分支。
- 不应被恢复成 `p[routerAddr]`。
- 这是同函数多 assembly 块下 slot alias/SSA 污染和分支作用域污染的共同表现。

## 9. setfeeRouter(address,bool)

原始语义：

```solidity
hash = keccak256(msg.sender);
check = o[hash];

if (check) {
    r[feeRouteradr] = _flag;
}
```

当前恢复存在问题：

```solidity
if (check) {
    check = r[feeRouteradr];
}

if (check) {
    r[feeRouteradr] = _flag;
}
```

问题点：

- 第一段权限读取应为 `check = o[hash]`。
- 当前错误使用了后续写入的 `r[feeRouteradr]`。
- 同样属于 slot alias/SSA 污染和分支作用域污染。

## 10. setToRouterFlag(address,bool)

原始语义：

```solidity
hash = keccak256(msg.sender);
check = o[hash];

if (check) {
    q[routerAddr] = _flag;
}
```

当前恢复存在问题：

```solidity
if (check) {
    check = q[routerAddr];
}

if (check) {
    q[routerAddr] = _flag;
}
```

问题点：

- 第一段权限读取应为 `check = o[hash]`。
- 当前错误使用了后续写入的 `q[routerAddr]`。
- 同样属于 slot alias/SSA 污染和分支作用域污染。

## 待解决问题汇总

1. 同函数多 assembly 块下，slot alias / SSA 污染。
2. `gt` / `lt` 与 `revert` 结合时，`require` 条件被错误布尔化。
3. `RequireOverlay` 渲染时没有正确保留 guard 作用域。
4. `div` 表达式恢复时只输出了除零 guard，原赋值丢失。
5. 多路径下相同事件、相同参数的重复 emit 需要合并优化。

其中第 1、2、3、4 点会影响语义正确性，应优先处理；第 5 点主要影响审计可读性。
