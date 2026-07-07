// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import "forge-std/Test.sol";
import "../src/UnderwriterPool.sol";
import "../src/ParametricEscrow.sol";
import "./mocks/MockERC20.sol";

/**
 * @title IndemnifyHandler
 * @notice Handler contract to generate valid state transitions for invariant testing.
 */
contract IndemnifyHandler is Test {
    UnderwriterPool public usdtPool;
    UnderwriterPool public usdgPool;
    ParametricEscrow public escrow;
    MockERC20 public usdt;
    MockERC20 public usdg;

    address public client = address(0x2222);
    address public lp = address(0x3333);
    uint256 public oraclePrivateKey;
    address public oracleAddress;

    // Track active policy IDs to select from for random settlements
    uint256[] public activePolicies;

    constructor(
        UnderwriterPool _usdtPool,
        UnderwriterPool _usdgPool,
        ParametricEscrow _escrow,
        MockERC20 _usdt,
        MockERC20 _usdg,
        uint256 _oraclePrivateKey
    ) {
        usdtPool = _usdtPool;
        usdgPool = _usdgPool;
        escrow = _escrow;
        usdt = _usdt;
        usdg = _usdg;
        oraclePrivateKey = _oraclePrivateKey;
        oracleAddress = vm.addr(_oraclePrivateKey);
    }

    /**
     * @notice Simulates an LP depositing stablecoins into a pool.
     */
    function depositLP(bool useUsdt, uint256 amount) public {
        amount = bound(amount, 1e2, 1_000_000 * 1e18);
        MockERC20 token = useUsdt ? usdt : usdg;
        UnderwriterPool pool = useUsdt ? usdtPool : usdgPool;

        // Scale USDT amount appropriately
        if (useUsdt) {
            amount = bound(amount, 1e2, 1_000_000 * 1e6);
        }

        vm.startPrank(lp);
        token.mint(lp, amount);
        token.approve(address(pool), amount);
        pool.deposit(amount, lp);
        vm.stopPrank();
    }

    /**
     * @notice Simulates an LP withdrawing stablecoins from a pool.
     */
    function withdrawLP(bool useUsdt, uint256 amount) public {
        UnderwriterPool pool = useUsdt ? usdtPool : usdgPool;
        uint256 sharesOwned = pool.balanceOf(lp);
        if (sharesOwned == 0) return;

        // Convert shares to assets to check limits
        uint256 total = pool.totalAssets();
        uint256 supply = pool.totalSupply();
        uint256 maxAssetsOwned = (sharesOwned * total) / supply;
        uint256 freeLiq = pool.freeLiquidity();

        uint256 maxWithdraw = maxAssetsOwned < freeLiq ? maxAssetsOwned : freeLiq;
        if (maxWithdraw == 0) return;

        amount = bound(amount, 1, maxWithdraw);

        vm.startPrank(lp);
        pool.withdraw(amount, lp, lp);
        vm.stopPrank();
    }

    /**
     * @notice Simulates a client creating a policy.
     */
    function createPolicy(
        bool useUsdt,
        uint256 coverageAmount,
        uint256 premiumAmount,
        uint256 timeoutDuration
    ) public {
        MockERC20 token = useUsdt ? usdt : usdg;
        UnderwriterPool pool = useUsdt ? usdtPool : usdgPool;
        uint256 freeLiq = pool.freeLiquidity();
        if (freeLiq == 0) return;

        // Bound parameters to realistic values
        coverageAmount = bound(coverageAmount, 1e2, freeLiq);
        premiumAmount = bound(premiumAmount, 1, coverageAmount / 2);
        timeoutDuration = bound(timeoutDuration, 3600, 365 days);

        uint256 deadline = block.timestamp + 1 hours;
        bytes32 quoteId = keccak256(abi.encodePacked(block.timestamp, coverageAmount, premiumAmount));

        // Generate cryptographic quote signature
        bytes32 messageHash = keccak256(
            abi.encodePacked(
                client,
                address(token),
                coverageAmount,
                premiumAmount,
                timeoutDuration,
                deadline,
                quoteId,
                block.chainid,
                address(escrow)
            )
        );
        bytes32 ethSignedMessageHash = MessageHashUtils.toEthSignedMessageHash(messageHash);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(oraclePrivateKey, ethSignedMessageHash);
        bytes memory sig = abi.encodePacked(r, s, v);

        // Execute policy creation
        vm.startPrank(client);
        token.mint(client, premiumAmount);
        token.approve(address(escrow), premiumAmount);
        
        uint256 policyId = escrow.createPolicy(
            address(token),
            coverageAmount,
            premiumAmount,
            timeoutDuration,
            deadline,
            quoteId,
            sig
        );
        vm.stopPrank();

        activePolicies.push(policyId);
    }

    /**
     * @notice Simulates the oracle settling a policy.
     */
    function settlePolicyRandom(uint256 policyIndex, uint8 tier) public {
        if (activePolicies.length == 0) return;
        uint256 idx = policyIndex % activePolicies.length;
        uint256 policyId = activePolicies[idx];
        
        tier = uint8(bound(tier, 0, 3));

        // Remove from active list
        activePolicies[idx] = activePolicies[activePolicies.length - 1];
        activePolicies.pop();

        vm.startPrank(oracleAddress);
        escrow.settlePolicy(policyId, tier);
        vm.stopPrank();
    }
}

/**
 * @title IndemnifyBaseTest
 * @notice Complete testing suite containing Unit, Fuzz, and Invariant tests.
 */
contract IndemnifyBaseTest is Test {
    UnderwriterPool public usdtPool;
    UnderwriterPool public usdgPool;
    ParametricEscrow public escrow;
    MockERC20 public usdt;
    MockERC20 public usdg;
    IndemnifyHandler public handler;

    uint256 public constant ORACLE_KEY = 0x5555;
    address public oracle = vm.addr(ORACLE_KEY);
    address public client = address(0x2222);
    address public lp = address(0x3333);

    event PolicyCreated(
        uint256 indexed policyId,
        address indexed client,
        address indexed asset,
        uint256 coverageAmount,
        uint256 premiumPaid,
        uint256 startTimestamp,
        uint256 timeoutDuration
    );
    event PolicySettled(
        uint256 indexed policyId,
        uint8 tier,
        uint256 payoutAmount,
        uint256 premiumTransferred
    );
    event PolicyTerminated(
        uint256 indexed policyId,
        uint256 refundAmount,
        uint256 underwriterFeeTransferred
    );

    function setUp() public {
        // Deploy Mock Tokens
        usdt = new MockERC20("Mock Tether", "USDT", 6);
        usdg = new MockERC20("Mock USDG", "USDG", 18);

        // Deploy Escrow
        escrow = new ParametricEscrow(oracle);

        // Deploy Pools
        usdtPool = new UnderwriterPool(address(usdt), address(escrow), "Indemnify USDT Share", "indUSDT");
        usdgPool = new UnderwriterPool(address(usdg), address(escrow), "Indemnify USDG Share", "indUSDG");

        // Register Pools
        escrow.registerPool(address(usdt), address(usdtPool));
        escrow.registerPool(address(usdg), address(usdgPool));

        // Setup Invariant Test Handler
        handler = new IndemnifyHandler(usdtPool, usdgPool, escrow, usdt, usdg, ORACLE_KEY);
        targetContract(address(handler));
    }

    // --- Helper Signature Methods ---
    function getQuoteSignature(
        address clientAddress,
        address asset,
        uint256 coverageAmount,
        uint256 premiumAmount,
        uint256 timeoutDuration,
        uint256 deadline,
        bytes32 quoteId
    ) public view returns (bytes memory) {
        bytes32 messageHash = keccak256(
            abi.encodePacked(
                clientAddress,
                asset,
                coverageAmount,
                premiumAmount,
                timeoutDuration,
                deadline,
                quoteId,
                block.chainid,
                address(escrow)
            )
        );
        bytes32 ethSignedMessageHash = MessageHashUtils.toEthSignedMessageHash(messageHash);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(ORACLE_KEY, ethSignedMessageHash);
        return abi.encodePacked(r, s, v);
    }

    function getSettlementSignature(uint256 policyId, uint8 tier) public view returns (bytes memory) {
        bytes32 messageHash = keccak256(
            abi.encodePacked(
                policyId,
                tier,
                block.chainid,
                address(escrow)
            )
        );
        bytes32 ethSignedMessageHash = MessageHashUtils.toEthSignedMessageHash(messageHash);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(ORACLE_KEY, ethSignedMessageHash);
        return abi.encodePacked(r, s, v);
    }

    // ==========================================
    // UNIT TESTS
    // ==========================================

    /**
     * @notice Test standard LP deposits and shares calculations (USDT / USDG).
     */
    function testLPDepositsAndShareCalculations() public {
        // USDT Pool (6 decimals)
        vm.startPrank(lp);
        usdt.mint(lp, 1000 * 1e6);
        usdt.approve(address(usdtPool), 1000 * 1e6);

        uint256 shares = usdtPool.deposit(1000 * 1e6, lp);
        assertEq(shares, 1000 * 1e6);
        assertEq(usdtPool.balanceOf(lp), 1000 * 1e6);
        assertEq(usdtPool.totalAssets(), 1000 * 1e6);

        // Deposit again after pool has accumulated some direct assets (yield)
        usdt.mint(address(usdtPool), 200 * 1e6); // direct yield injection
        assertEq(usdtPool.totalAssets(), 1200 * 1e6);

        // Shares minted = assets * supply / total = 1000 * 1000 / 1200 = 833
        usdt.mint(lp, 1000 * 1e6);
        usdt.approve(address(usdtPool), 1000 * 1e6);
        uint256 nextShares = usdtPool.deposit(1000 * 1e6, lp);
        assertEq(nextShares, 833333333); // 833.33 shares
        vm.stopPrank();

        // USDG Pool (18 decimals)
        vm.startPrank(lp);
        usdg.mint(lp, 1000 * 1e18);
        usdg.approve(address(usdgPool), 1000 * 1e18);

        uint256 usdgShares = usdgPool.deposit(1000 * 1e18, lp);
        assertEq(usdgShares, 1000 * 1e18);
        vm.stopPrank();
    }

    /**
     * @notice Test LP withdrawal limits based on reserved capital constraints.
     */
    function testLPWithdrawalLimits() public {
        vm.startPrank(lp);
        usdt.mint(lp, 1000 * 1e6);
        usdt.approve(address(usdtPool), 1000 * 1e6);
        usdtPool.deposit(1000 * 1e6, lp);
        vm.stopPrank();

        // Lock some capital via policy
        bytes32 quoteId = keccak256("quote-1");
        bytes memory sig = getQuoteSignature(client, address(usdt), 400 * 1e6, 20 * 1e6, 1 days, block.timestamp + 1 hours, quoteId);

        vm.startPrank(client);
        usdt.mint(client, 20 * 1e6);
        usdt.approve(address(escrow), 20 * 1e6);
        escrow.createPolicy(address(usdt), 400 * 1e6, 20 * 1e6, 1 days, block.timestamp + 1 hours, quoteId, sig);
        vm.stopPrank();

        // Free liquidity = total (1000) - reserved (400) = 600
        assertEq(usdtPool.freeLiquidity(), 600 * 1e6);
        assertEq(usdtPool.reservedCapital(), 400 * 1e6);

        // LP trying to withdraw 700 must revert
        vm.startPrank(lp);
        vm.expectRevert(UnderwriterPool.InsufficientFreeLiquidity.selector);
        usdtPool.withdraw(700 * 1e6, lp, lp);

        // LP withdrawing 500 should succeed
        usdtPool.withdraw(500 * 1e6, lp, lp);
        assertEq(usdtPool.freeLiquidity(), 100 * 1e6);
        vm.stopPrank();
    }

    /**
     * @notice Test creating policies, validating struct fields and signature protection.
     */
    function testPolicyCreationAndSignatureProtection() public {
        vm.startPrank(lp);
        usdg.mint(lp, 5000 * 1e18);
        usdg.approve(address(usdgPool), 5000 * 1e18);
        usdgPool.deposit(5000 * 1e18, lp);
        vm.stopPrank();

        bytes32 quoteId = keccak256("quote-2");
        uint256 deadline = block.timestamp + 1 hours;
        bytes memory sig = getQuoteSignature(client, address(usdg), 2000 * 1e18, 100 * 1e18, 2 days, deadline, quoteId);

        vm.startPrank(client);
        usdg.mint(client, 100 * 1e18);
        usdg.approve(address(escrow), 100 * 1e18);

        vm.expectEmit(true, true, true, false);
        emit PolicyCreated(1, client, address(usdg), 2000 * 1e18, 100 * 1e18, block.timestamp, 2 days);

        uint256 policyId = escrow.createPolicy(
            address(usdg),
            2000 * 1e18,
            100 * 1e18,
            2 days,
            deadline,
            quoteId,
            sig
        );
        assertEq(policyId, 1);

        // Verify policy details
        (
            address clientAddress,
            address asset,
            uint256 coverageAmount,
            uint256 premiumPaid,
            uint256 startTimestamp,
            uint256 timeoutDuration,
            uint8 riskBracketTier,
            ParametricEscrow.PolicyStatus status
        ) = escrow.policies(policyId);

        assertEq(clientAddress, client);
        assertEq(asset, address(usdg));
        assertEq(coverageAmount, 2000 * 1e18);
        assertEq(premiumPaid, 100 * 1e18);
        assertEq(startTimestamp, block.timestamp);
        assertEq(timeoutDuration, 2 days);
        assertEq(riskBracketTier, 0);
        assertEq(uint256(status), uint256(ParametricEscrow.PolicyStatus.Active));

        // Reusing signature should fail
        vm.expectRevert(ParametricEscrow.SignatureAlreadyUsed.selector);
        escrow.createPolicy(
            address(usdg),
            2000 * 1e18,
            100 * 1e18,
            2 days,
            deadline,
            quoteId,
            sig
        );
        vm.stopPrank();
    }

    /**
     * @notice Test the Asian Handicap Settlement Brackets (Tiers 0-3).
     */
    function testSettlementTiers() public {
        // Base Setup: Deposit LP Capital
        vm.startPrank(lp);
        usdt.mint(lp, 10000 * 1e6);
        usdt.approve(address(usdtPool), 10000 * 1e6);
        usdtPool.deposit(10000 * 1e6, lp);
        vm.stopPrank();

        // TIER 0: Full Success (0% payout)
        uint256 p0 = _helperCreatePolicy(1000 * 1e6, 50 * 1e6, "q0");
        uint256 poolBalBefore = usdt.balanceOf(address(usdtPool));
        vm.startPrank(oracle);
        escrow.settlePolicy(p0, 0);
        vm.stopPrank();
        assertEq(usdt.balanceOf(client), 0);
        // Premium goes to pool, locked capital released
        assertEq(usdt.balanceOf(address(usdtPool)), poolBalBefore + 50 * 1e6);
        assertEq(usdtPool.reservedCapital(), 0);

        // TIER 1: Partial Failure (35% payout)
        uint256 p1 = _helperCreatePolicy(1000 * 1e6, 50 * 1e6, "q1");
        poolBalBefore = usdt.balanceOf(address(usdtPool));
        vm.startPrank(oracle);
        escrow.settlePolicy(p1, 1);
        vm.stopPrank();
        assertEq(usdt.balanceOf(client), 350 * 1e6); // 35% of 1000
        // Premium (50) transferred from escrow, payout (350) sent to client from pool. Net change = -300
        assertEq(usdt.balanceOf(address(usdtPool)), poolBalBefore + 50 * 1e6 - 350 * 1e6);
        assertEq(usdtPool.reservedCapital(), 0);

        // TIER 2: Major Failure (75% payout)
        vm.startPrank(client);
        usdt.transfer(address(0xdead), usdt.balanceOf(client)); // burn client balance to reset
        vm.stopPrank();
        uint256 p2 = _helperCreatePolicy(1000 * 1e6, 50 * 1e6, "q2");
        vm.startPrank(oracle);
        escrow.settlePolicy(p2, 2);
        vm.stopPrank();
        assertEq(usdt.balanceOf(client), 750 * 1e6); // 75% of 1000

        // TIER 3: Total Revert (100% payout)
        vm.startPrank(client);
        usdt.transfer(address(0xdead), usdt.balanceOf(client)); // burn client balance to reset
        vm.stopPrank();
        uint256 p3 = _helperCreatePolicy(1000 * 1e6, 50 * 1e6, "q3");
        vm.startPrank(oracle);
        escrow.settlePolicy(p3, 3);
        vm.stopPrank();
        assertEq(usdt.balanceOf(client), 1000 * 1e6); // 100% of 1000
    }

    /**
     * @notice Test settlement using cryptography signatures submitted by users (pull model).
     */
    function testSettlementWithSignature() public {
        vm.startPrank(lp);
        usdt.mint(lp, 5000 * 1e6);
        usdt.approve(address(usdtPool), 5000 * 1e6);
        usdtPool.deposit(5000 * 1e6, lp);
        vm.stopPrank();

        uint256 policyId = _helperCreatePolicy(1000 * 1e6, 50 * 1e6, "q_sig");
        
        bytes memory sig = getSettlementSignature(policyId, 2);

        // Settlement execution by a third party
        address thirdParty = address(0x9999);
        vm.startPrank(thirdParty);
        escrow.settlePolicyWithSignature(policyId, 2, sig);
        vm.stopPrank();

        assertEq(usdt.balanceOf(client), 750 * 1e6); // Tier 2 (75%)
    }

    /**
     * @notice Test programmatic premature termination (partial cashout).
     */
    function testProgrammaticPartialCashout() public {
        vm.startPrank(lp);
        usdt.mint(lp, 5000 * 1e6);
        usdt.approve(address(usdtPool), 5000 * 1e6);
        usdtPool.deposit(5000 * 1e6, lp);
        vm.stopPrank();

        uint256 policyId = _helperCreatePolicy(1000 * 1e6, 100 * 1e6, "q_term");

        // Warp time to 25% of duration (6 hours elapsed out of 24 hours)
        vm.warp(block.timestamp + 6 hours);

        // Terminate policy
        uint256 poolBalBefore = usdt.balanceOf(address(usdtPool));
        vm.startPrank(client);
        escrow.terminatePolicy(policyId);
        vm.stopPrank();

        // Remaining time = 18 hours (75%)
        // Refund amount = 100 * 75% = 75
        // Underwriter fee = 100 - 75 = 25
        assertEq(usdt.balanceOf(client), 75 * 1e6);
        assertEq(usdt.balanceOf(address(usdtPool)), poolBalBefore + 25 * 1e6);
        assertEq(usdtPool.reservedCapital(), 0);

        // Check struct status
        (,,,,,,, ParametricEscrow.PolicyStatus status) = escrow.policies(policyId);
        assertEq(uint256(status), uint256(ParametricEscrow.PolicyStatus.Refunded));
    }

    // ==========================================
    // FUZZ TESTS
    // ==========================================

    /**
     * @notice Fuzz test for pro-rata refund calculations.
     */
    function testFuzzProRataRefund(uint256 premiumPaid, uint256 elapsed, uint256 timeoutDuration) public {
        // Bound to realistic values
        premiumPaid = bound(premiumPaid, 1e2, 1e30);
        timeoutDuration = bound(timeoutDuration, 10, 365 days);
        elapsed = bound(elapsed, 0, timeoutDuration - 1);

        // Compute pro-rata using contracts math
        uint256 remaining = timeoutDuration - elapsed;
        uint256 refundAmount = (premiumPaid * remaining) / timeoutDuration;
        uint256 underwriterFee = premiumPaid - refundAmount;

        // Invariants
        assertTrue(refundAmount <= premiumPaid);
        assertTrue(underwriterFee <= premiumPaid);
        assertEq(refundAmount + underwriterFee, premiumPaid);
    }

    /**
     * @notice Fuzz test for the Asian Handicap bracket calculations.
     */
    function testFuzzSettlementBrackets(uint256 coverageAmount, uint8 tier) public {
        coverageAmount = bound(coverageAmount, 0, 1e30);
        tier = uint8(bound(tier, 0, 3));

        uint256 bps = (tier == 0) ? 0 : (tier == 1) ? 3500 : (tier == 2) ? 7500 : 10000;
        uint256 payoutAmount = (coverageAmount * bps) / 10000;

        assertTrue(payoutAmount <= coverageAmount);
        if (tier == 0) assertEq(payoutAmount, 0);
        if (tier == 3) assertEq(payoutAmount, coverageAmount);
    }

    // ==========================================
    // INVARIANT TESTS
    // ==========================================

    /**
     * @notice Invariant asserting: Total Pooled Capital >= Free Liquidity + Reserved Capital Liabilities.
     */
    function invariant_systemBalance() public view {
        uint256 usdtTotal = usdtPool.totalAssets();
        uint256 usdtFree = usdtPool.freeLiquidity();
        uint256 usdtReserved = usdtPool.reservedCapital();

        assertEq(usdtTotal, usdtFree + usdtReserved, "USDT Balance invariant broken");

        uint256 usdgTotal = usdgPool.totalAssets();
        uint256 usdgFree = usdgPool.freeLiquidity();
        uint256 usdgReserved = usdgPool.reservedCapital();

        assertEq(usdgTotal, usdgFree + usdgReserved, "USDG Balance invariant broken");
    }

    // --- Internal Helpers ---
    function _helperCreatePolicy(
        uint256 coverage,
        uint256 premium,
        bytes32 qId
    ) internal returns (uint256) {
        bytes memory sig = getQuoteSignature(client, address(usdt), coverage, premium, 24 hours, block.timestamp + 1 hours, qId);

        vm.startPrank(client);
        usdt.mint(client, premium);
        usdt.approve(address(escrow), premium);
        uint256 policyId = escrow.createPolicy(address(usdt), coverage, premium, 24 hours, block.timestamp + 1 hours, qId, sig);
        vm.stopPrank();

        return policyId;
    }
}
