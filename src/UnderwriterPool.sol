// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/**
 * @title UnderwriterPool
 * @author Project Indemnify
 * @notice Yield-bearing vault where LPs deposit stablecoins to underwrite risk.
 * It manages capital reservation states to guarantee active escrow liabilities.
 */
contract UnderwriterPool is ERC20, ReentrancyGuard {
    using SafeERC20 for IERC20;

    // --- Custom Errors ---
    error ZeroAddress();
    error ZeroDeposit();
    error ZeroWithdraw();
    error ZeroShares();
    error InsufficientFreeLiquidity();
    error OnlyEscrow();
    error InvalidPayoutAmount();

    // --- Events ---
    event Deposit(address indexed sender, address indexed owner, uint256 assets, uint256 shares);
    event Withdraw(
        address indexed sender,
        address indexed receiver,
        address indexed owner,
        uint256 assets,
        uint256 shares
    );
    event CapitalLocked(uint256 amount);
    event CapitalReleased(uint256 amount);
    event Payout(address indexed recipient, uint256 payoutAmount, uint256 coverageAmount);

    // --- State Variables ---
    IERC20 public immutable underlyingAsset;
    address public immutable escrow;
    uint256 public reservedCapital;
    uint8 private immutable _decimals;

    // --- Modifiers ---
    modifier onlyEscrow() {
        if (msg.sender != escrow) revert OnlyEscrow();
        _;
    }

    /**
     * @notice Constructor to initialize the pool.
     * @param assetAddress Address of the underlying stablecoin (USDT/USDG).
     * @param escrowAddress Address of the ParametricEscrow contract.
     * @param name_ Name of the share token.
     * @param symbol_ Symbol of the share token.
     */
    constructor(
        address assetAddress,
        address escrowAddress,
        string memory name_,
        string memory symbol_
    ) ERC20(name_, symbol_) {
        if (assetAddress == address(0) || escrowAddress == address(0)) revert ZeroAddress();
        underlyingAsset = IERC20(assetAddress);
        escrow = escrowAddress;
        _decimals = IERC20Metadata(assetAddress).decimals();
    }

    // --- ERC20 Override ---
    /**
     * @notice Override decimals to match the underlying asset.
     */
    function decimals() public view override returns (uint8) {
        return _decimals;
    }

    // --- LP Core Functions ---

    /**
     * @notice Deposit stablecoins to mint yield-bearing pool shares.
     * @param assets Amount of underlying stablecoins to deposit.
     * @param receiver Address to receive the share tokens.
     */
    function deposit(uint256 assets, address receiver) external nonReentrant returns (uint256 shares) {
        if (assets == 0) revert ZeroDeposit();
        if (receiver == address(0)) revert ZeroAddress();

        uint256 total = totalAssets();
        uint256 supply = totalSupply();

        if (supply == 0) {
            shares = assets;
        } else {
            shares = (assets * supply) / total;
        }

        if (shares == 0) revert ZeroShares();

        underlyingAsset.safeTransferFrom(msg.sender, address(this), assets);
        _mint(receiver, shares);

        emit Deposit(msg.sender, receiver, assets, shares);
    }

    /**
     * @notice Withdraw stablecoins by burning vault shares.
     * @dev Restricted by the amount of free (unreserved) liquidity in the pool.
     * @param assets Amount of underlying stablecoins to withdraw.
     * @param receiver Address to receive the stablecoins.
     * @param owner Address that owns the shares being burned.
     */
    function withdraw(
        uint256 assets,
        address receiver,
        address owner
    ) external nonReentrant returns (uint256 shares) {
        if (assets == 0) revert ZeroWithdraw();
        if (receiver == address(0)) revert ZeroAddress();

        uint256 total = totalAssets();
        if (assets > freeLiquidity()) revert InsufficientFreeLiquidity();

        uint256 supply = totalSupply();
        // Round up shares to burn to protect remaining LPs from rounding exploits
        shares = (assets * supply + (total - 1)) / total;

        if (msg.sender != owner) {
            uint256 allowed = allowance(owner, msg.sender);
            _approve(owner, msg.sender, allowed - shares);
        }

        _burn(owner, shares);
        underlyingAsset.safeTransfer(receiver, assets);

        emit Withdraw(msg.sender, receiver, owner, assets, shares);
    }

    // --- Escrow Core Hook Functions (Restricted) ---

    /**
     * @notice Locks a portion of the pool's capital to cover an active policy.
     * @param amount Capital amount to transition into reserved state.
     */
    function lockCapital(uint256 amount) external onlyEscrow {
        if (amount > freeLiquidity()) revert InsufficientFreeLiquidity();
        reservedCapital += amount;
        emit CapitalLocked(amount);
    }

    /**
     * @notice Releases reserved capital back to free liquidity.
     * @param amount Capital amount to unlock.
     */
    function releaseCapital(uint256 amount) external onlyEscrow {
        reservedCapital -= amount;
        emit CapitalReleased(amount);
    }

    /**
     * @notice Pays out a claim to a client and releases the associated reserved capital.
     * @dev Automatically returns the unclaimed portion of the reserved capital back to free liquidity.
     * @param recipient Address of the client receiving the claim payout.
     * @param payoutAmount Amount of assets to transfer.
     * @param coverageAmount Total amount that was reserved for the policy.
     */
    function payout(
        address recipient,
        uint256 payoutAmount,
        uint256 coverageAmount
    ) external onlyEscrow nonReentrant {
        if (recipient == address(0)) revert ZeroAddress();
        if (coverageAmount < payoutAmount) revert InvalidPayoutAmount();

        // Release the full coverage amount from reservation
        reservedCapital -= coverageAmount;

        // Transfer the payout amount to the recipient
        underlyingAsset.safeTransfer(recipient, payoutAmount);

        emit Payout(recipient, payoutAmount, coverageAmount);
    }

    // --- View Functions ---

    /**
     * @notice Total underlying assets held by the contract.
     */
    function totalAssets() public view returns (uint256) {
        return underlyingAsset.balanceOf(address(this));
    }

    /**
     * @notice Unreserved capital available for withdrawals or new policy locks.
     */
    function freeLiquidity() public view returns (uint256) {
        uint256 total = totalAssets();
        uint256 reserved = reservedCapital;
        if (total < reserved) return 0; // Guard against temporary state skew
        return total - reserved;
    }
}
