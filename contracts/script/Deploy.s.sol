// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {OrchestratorRegistry} from "../Orchestrator.sol";
import {Guardian} from "../Guardian.sol";
import {ReputationSBT} from "../ReputationSBT.sol";

interface Vm {
    function startBroadcast() external;
    function stopBroadcast() external;
}

address constant VM_ADDRESS = address(uint160(uint256(keccak256("hevm cheat code"))));
Vm constant vm = Vm(VM_ADDRESS);

/// @title Pramãna Deploy Script (Forge-compatible)
/// @notice Run with: forge script contracts/script/Deploy.s.sol --rpc-url $RPC_URL --broadcast
///         For Anvil local: forge script contracts/script/Deploy.s.sol --rpc-url http://127.0.0.1:8545 --broadcast
contract DeployPramana {
    OrchestratorRegistry public orchestrator;
    Guardian             public guardian;
    ReputationSBT        public sbt;

    /// @notice Deploy all three contracts in order.
    ///         Returns addresses for .env configuration.
    function run() external returns (address, address, address) {
        vm.startBroadcast();

        // 1. Orchestrator (no deps)
        orchestrator = new OrchestratorRegistry();

        // 2. Guardian (needs orchestrator address)
        guardian = new Guardian(address(orchestrator));

        // 3. SBT (needs orchestrator address)
        sbt = new ReputationSBT(address(orchestrator));

        vm.stopBroadcast();

        return (address(orchestrator), address(guardian), address(sbt));
    }
}
