// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import "forge-std/Script.sol";
import "../src/ParametricEscrow.sol";
import "../src/UnderwriterPool.sol";
import "../test/mocks/MockERC20.sol";

/**
 * @title DeployIndemnify
 * @notice Forge script to deploy the mock stablecoins, ParametricEscrow,
 * and UnderwriterPools, and register the configurations.
 */
contract DeployIndemnify is Script {
    function run() external {
        // Retrieve private key from env or default to Anvil's primary account
        uint256 deployerPrivateKey = vm.envOr(
            "DEPLOYER_PRIVATE_KEY",
            uint256(0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80)
        );
        
        // Retrieve oracle address from env or default to Anvil's secondary account
        address oracleAddress = vm.envOr(
            "ORACLE_ADDRESS",
            address(0x70997970C51812dc3A010C7d01b50e0d17dc79C8)
        );

        address deployer = msg.sender;
        console.log("Starting deployment from address:", deployer);
        console.log("Analytical Oracle address set to:", oracleAddress);

        vm.startBroadcast();

        // 1. Deploy Mock Stablecoins
        MockERC20 usdt = new MockERC20("Mock Tether USD", "USDT", 6);
        console.log("Mock USDT deployed at:", address(usdt));

        MockERC20 usdg = new MockERC20("Mock USDG Stablecoin", "USDG", 18);
        console.log("Mock USDG deployed at:", address(usdg));

        // 2. Deploy ParametricEscrow settlement engine
        ParametricEscrow escrow = new ParametricEscrow(oracleAddress);
        console.log("ParametricEscrow deployed at:", address(escrow));

        // 3. Deploy UnderwriterPool for USDT (indUSDT)
        UnderwriterPool usdtPool = new UnderwriterPool(
            address(usdt),
            address(escrow),
            "Indemnify USDT Share",
            "indUSDT"
        );
        console.log("UnderwriterPool USDT deployed at:", address(usdtPool));

        // 4. Deploy UnderwriterPool for USDG (indUSDG)
        UnderwriterPool usdgPool = new UnderwriterPool(
            address(usdg),
            address(escrow),
            "Indemnify USDG Share",
            "indUSDG"
        );
        console.log("UnderwriterPool USDG deployed at:", address(usdgPool));

        // 5. Register Pools on ParametricEscrow
        escrow.registerPool(address(usdt), address(usdtPool));
        console.log("Registered USDT Pool on Escrow.");

        escrow.registerPool(address(usdg), address(usdgPool));
        console.log("Registered USDG Pool on Escrow.");

        vm.stopBroadcast();
        console.log("Indemnify Settlement Layer deployment completed successfully!");
    }
}
