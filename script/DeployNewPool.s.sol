// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import "forge-std/Script.sol";
import "../src/ParametricEscrow.sol";
import "../src/UnderwriterPool.sol";

contract DeployNewPool is Script {
    function run() external {
        uint256 deployerPrivateKey = vm.envUint("DEPLOYER_PRIVATE_KEY");

        // The actual deployed addresses
        address escrowAddress = 0x4B218726007858FC77fb2Aa476bd547d13f14670;
        address usdt0Address = 0x779Ded0c9e1022225f8E0630b35a9b54bE713736;

        vm.startBroadcast(deployerPrivateKey);

        // 1. Deploy the new pool
        UnderwriterPool newPool = new UnderwriterPool(
            usdt0Address,
            escrowAddress,
            "Indemnify USDT0 Share",
            "indUSDT0"
        );
        console.log("New UnderwriterPool USDT0 deployed at:", address(newPool));

        // 2. Register the new pool in the Escrow contract
        ParametricEscrow escrow = ParametricEscrow(escrowAddress);
        escrow.registerPool(usdt0Address, address(newPool));
        console.log("Registered NEW USDT0 Pool on Escrow.");

        vm.stopBroadcast();
    }
}
