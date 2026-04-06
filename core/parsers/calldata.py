"""
core/parsers/calldata.py — ABI-aware Calldata Parser

Decodes arbitrary smart contract calldata using a curated registry of top DeFi
function selectors. Enables L1 agents to reason about intent (addresses, amounts)
rather than raw hex strings.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from eth_abi import decode
from eth_utils import encode_hex, function_abi_to_4byte_selector
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger("pramana.parser")


def _safe_hex_bytes(hex_str: str) -> bytes:
    """
    Safely convert hex string to bytes.
    Returns empty bytes on any failure — never raises.
    Handles: 0x-prefixed, unprefixed, odd-length, non-hex, None.
    """
    if not hex_str:
        return b""
    try:
        clean = str(hex_str).replace("0x", "").replace(" ", "").strip()
        if len(clean) % 2 != 0:
            clean = "0" + clean
        return bytes.fromhex(clean)
    except (ValueError, AttributeError, TypeError):
        return b""


class DecodedCall(BaseModel):
    """Structured representation of a decoded transaction call."""

    selector: str
    name: str
    protocol: str
    signature: str
    params: Dict[str, Any]
    risk: str = "normal"

    # Store raw params for easy access
    addresses: List[str] = []
    strings: List[str] = []

    model_config = ConfigDict(extra="allow")


class CalldataParser:
    """
    Parses Ethereum calldata into structured intents using a local registry.
    """

    def __init__(self):
        self._registry = self._load_registry()

    def _load_registry(self) -> dict:
        """Load the selector registry from JSON."""
        registry_path = Path(__file__).parent / "selector_registry.json"
        try:
            with open(registry_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Remove comments
            return {k: v for k, v in data.items() if not k.startswith("_")}
        except Exception as e:
            logger.error(f"Failed to load selector registry: {e}")
            return {}

    def decode(self, calldata: str) -> Optional[DecodedCall]:
        """
        Attempt to decode raw hex calldata into a structured DecodedCall.
        """
        if not calldata or len(calldata) < 10:
            return None

        calldata = calldata.lower()
        if calldata.startswith("0x"):
            calldata = calldata[2:]

        selector = calldata[:8]
        args_data = _safe_hex_bytes(calldata[8:])

        if selector not in self._registry:
            return None

        entry = self._registry[selector]
        params_abi = entry.get("params", [])

        types = []
        names = []
        for p in params_abi:
            # Handle basic tuples (for Uniswap struct params)
            if p["type"] == "tuple":
                components = ",".join([c["type"] for c in p.get("components", [])])
                types.append(f"({components})")
            elif p["type"] == "tuple[]":
                components = ",".join([c["type"] for c in p.get("components", [])])
                types.append(f"({components})[]")
            else:
                types.append(p["type"])
            names.append(p["name"])

        try:
            if not types:
                decoded_values = ()
            else:
                decoded_values = decode(types, args_data)

            params_dict = dict(zip(names, decoded_values))

            # Format addresses and sanitize bytes
            clean_params = {}
            for k, v in params_dict.items():
                if isinstance(v, bytes):
                    clean_params[k] = "0x" + v.hex()
                elif isinstance(v, list) and len(v) > 0 and isinstance(v[0], bytes):
                    clean_params[k] = ["0x" + item.hex() for item in v]
                else:
                    clean_params[k] = v

            call = DecodedCall(
                selector=selector,
                name=entry["name"],
                protocol=entry["protocol"],
                signature=entry["signature"],
                params=clean_params,
                risk=entry.get("risk", "normal"),
            )

            # Extract common parameter types for rapid access
            call.addresses = self.extract_addresses(call)
            call.strings = self.extract_string_parameters(call)
            return call

        except Exception as e:
            logger.debug(f"Failed to decode {selector} args: {e}")
            return None

    def extract_addresses(self, call: DecodedCall) -> List[str]:
        """Extract all Ethereum addresses from the decoded parameters."""
        addresses = []

        def _walk(obj):
            if isinstance(obj, str) and obj.startswith("0x") and len(obj) == 42:
                addresses.append(obj.lower())
            elif isinstance(obj, dict):
                for v in obj.values():
                    _walk(v)
            elif isinstance(obj, (list, tuple)):
                for v in obj:
                    _walk(v)

        _walk(call.params)
        return list(set(addresses))

    def extract_string_parameters(self, call: DecodedCall) -> List[str]:
        """Extract all string parameters (useful for InjectionAgent checking)."""
        strings = []

        def _walk(obj):
            if isinstance(obj, str) and not obj.startswith("0x"):
                strings.append(obj)
            elif isinstance(obj, dict):
                for v in obj.values():
                    _walk(v)
            elif isinstance(obj, (list, tuple)):
                for v in obj:
                    _walk(v)

        _walk(call.params)
        return strings

    def decode_batch_calls(self, calldata: str) -> List[DecodedCall]:
        """
        Deep decode of AA Smart Account batch executions.
        Extracts the inner calls made by the bundler/entrypoint.
        """
        top_call = self.decode(calldata)
        if not top_call:
            return []

        calls = [top_call]

        # Handle Account Abstraction Batch Executions
        if top_call.name == "executeBatch":
            # Gnosis/SimpleAccount/LightAccount/Kernel batch formats
            dests = top_call.params.get("dest", top_call.params.get("to", []))
            data_payloads = top_call.params.get("data", top_call.params.get("func", []))

            # Normal array-of-arrays batch
            if (
                isinstance(data_payloads, list)
                and len(data_payloads) > 0
                and isinstance(data_payloads[0], (bytes, str))
            ):
                for hex_data in data_payloads:
                    if isinstance(hex_data, bytes):
                        hex_str = hex_data.hex()
                    else:
                        hex_str = hex_data.replace("0x", "")

                    if len(hex_str) >= 8:
                        inner = self.decode(hex_str)
                        if inner:
                            calls.append(inner)

            # Kernel tuple struct batch (calls: tuple[])
            if "calls" in top_call.params:
                for batch_item in top_call.params["calls"]:
                    # Item is (address, value, data)
                    if len(batch_item) >= 3:
                        inner_data = batch_item[2]
                        if isinstance(inner_data, bytes):
                            hex_str = inner_data.hex()
                        else:
                            hex_str = inner_data.replace("0x", "")

                        if len(hex_str) >= 8:
                            inner = self.decode(hex_str)
                            if inner:
                                calls.append(inner)

        # Handle Multicall
        elif top_call.name == "multicall":
            data_payloads = top_call.params.get("data", [])
            for hex_data in data_payloads:
                if isinstance(hex_data, bytes):
                    hex_str = hex_data.hex()
                else:
                    hex_str = hex_data.replace("0x", "")

                if len(hex_str) >= 8:
                    inner = self.decode(hex_str)
                    if inner:
                        calls.append(inner)

        return calls


# Singleton instance
parser = CalldataParser()


def decode_calldata(hex_str: str) -> Optional[DecodedCall]:
    """Convenience function for top-level decoding."""
    return parser.decode(hex_str)


def decode_all_calls(hex_str: str) -> List[DecodedCall]:
    """Convenience function for deep decoding (including batches)."""
    return parser.decode_batch_calls(hex_str)
