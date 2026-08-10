// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

type SnapshotId is uint256;

struct Snapshot {
    SnapshotId id;
    address account;
    uint256 balance;
    bytes32 noteHash;
}

enum AccountFlag {
    None,
    Frozen,
    Trusted
}

contract TypedSnapshotERC20 {
    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
    event SnapshotTaken(SnapshotId indexed id, address indexed account, uint256 balance);

    string public name = "Typed Snapshot Token";
    string public symbol = "TST";
    uint8 public constant decimals = 18;
    uint256 public totalSupply;
    bool public paused;
    int256 public signedSupplyDelta;
    bytes4 public constant TRANSFER_SELECTOR = bytes4(keccak256("transfer(address,uint256)"));
    bytes32 public domain;
    bytes public metadataBlob;
    uint256[3] public lastThreeTransfers;

    Snapshot[] public snapshots;
    mapping(address account => uint256 balance) public balanceOf;
    mapping(address owner => mapping(address spender => uint256 value)) public allowance;
    mapping(address account => AccountFlag flag) public flags;

    constructor(uint256 initialSupply, bytes memory metadata) {
        metadataBlob = metadata;
        domain = keccak256(abi.encode(block.chainid, address(this), "TST"));
        _mint(msg.sender, initialSupply);
    }

    function transfer(address to, uint256 value) external returns (bool) {
        _transferWithHook(msg.sender, to, value, _identityFee);
        return true;
    }

    function approve(address spender, uint256 value) external returns (bool) {
        allowance[msg.sender][spender] = value;
        emit Approval(msg.sender, spender, value);
        return true;
    }

    function transferFrom(address from, address to, uint256 value) external returns (bool) {
        uint256 current = allowance[from][msg.sender];
        require(current >= value, "allowance");
        allowance[from][msg.sender] = current - value;
        _transferWithHook(from, to, value, _identityFee);
        return true;
    }

    function batchTransfer(address[] calldata receivers, uint256[] calldata values) external returns (uint256 totalMoved) {
        require(receivers.length == values.length, "length");
        for (uint256 i = 0; i < receivers.length; ++i) {
            _transferWithHook(msg.sender, receivers[i], values[i], _identityFee);
            totalMoved += values[i];
        }
    }

    function takeSnapshot(address account, string calldata note) external returns (SnapshotId id) {
        id = SnapshotId.wrap(snapshots.length + 1);
        snapshots.push(Snapshot({
            id: id,
            account: account,
            balance: balanceOf[account],
            noteHash: keccak256(bytes(note))
        }));
        emit SnapshotTaken(id, account, balanceOf[account]);
    }

    function copySnapshot(uint256 index) external view returns (Snapshot memory snap) {
        Snapshot storage stored = snapshots[index];
        snap = stored;
    }

    function snapshotCount() external view returns (uint256) {
        return snapshots.length;
    }

    function castExamples(address payable payableAccount, uint256 raw) external pure returns (uint128, bytes20, address payable) {
        uint128 narrowed = uint128(raw);
        bytes20 accountBytes = bytes20(address(payableAccount));
        return (narrowed, accountBytes, payable(payableAccount));
    }

    function _transferWithHook(
        address from,
        address to,
        uint256 value,
        function(uint256) pure returns (uint256) feeFn
    ) internal {
        require(flags[from] != AccountFlag.Frozen, "frozen");
        uint256 fee = feeFn(value);
        uint256 debit = value + fee;
        uint256 bal = balanceOf[from];
        require(bal >= debit, "balance");
        balanceOf[from] = bal - debit;
        balanceOf[to] += value;
        signedSupplyDelta -= int256(fee);
        lastThreeTransfers[0] = lastThreeTransfers[1];
        lastThreeTransfers[1] = lastThreeTransfers[2];
        lastThreeTransfers[2] = value;
        emit Transfer(from, to, value);
    }

    function _identityFee(uint256) internal pure returns (uint256) {
        return 0;
    }

    function _mint(address to, uint256 value) internal {
        balanceOf[to] += value;
        totalSupply += value;
        signedSupplyDelta += int256(value);
        emit Transfer(address(0), to, value);
    }
}

