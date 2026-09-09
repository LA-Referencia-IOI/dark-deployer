"""Idempotent Authority + dARK deployment job used by the v2 apps Compose group."""

from __future__ import annotations

import json
import os
from pathlib import Path

from web3 import Web3


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def _artifact(name: str) -> tuple[list, str]:
    root = Path(os.environ.get("CONTRACT_ARTIFACTS_DIR", "/contracts"))
    return json.loads((root / f"{name}ABI.json").read_text()), (root / f"{name}Bytecode.txt").read_text().strip()


def _handoff_path() -> Path:
    path = Path(_required("CONTRACT_HANDOFF_FILE"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _verify_existing(web3: Web3, handoff: dict, chain_id: int) -> bool:
    if handoff.get("chain_id") != chain_id:
        return False
    addresses = handoff.get("contracts", {})
    return all(web3.eth.get_code(addresses.get(name, "")) not in (b"", b"\x00") for name in ("authority", "dark"))


def _write_runtime_env(path: Path, handoff: dict) -> None:
    contracts = handoff["contracts"]
    path.write_text(
        f"DARK_AUTHORITY_ADDRESS={contracts['authority']}\n"
        f"DARK_CONTRACT_ADDRESS={contracts['dark']}\n"
    )
    path.chmod(0o644)


def main() -> None:
    rpc = _required("DARK_RPC_URL")
    chain_id = int(_required("DARK_CHAIN_ID"))
    handoff_path = _handoff_path()
    runtime_env = Path(_required("CONTRACT_RUNTIME_ENV_FILE"))
    runtime_env.parent.mkdir(parents=True, exist_ok=True)
    web3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 15}))
    if not web3.is_connected():
        raise RuntimeError(f"RPC is unavailable: {rpc}")
    if web3.eth.chain_id != chain_id:
        raise RuntimeError(f"RPC chain ID {web3.eth.chain_id} differs from expected {chain_id}")
    if handoff_path.exists():
        handoff = json.loads(handoff_path.read_text())
        if _verify_existing(web3, handoff, chain_id):
            _write_runtime_env(runtime_env, handoff)
            print("existing contract handoff verified")
            return
        raise RuntimeError("existing contract handoff does not match live chain; refusing redeployment")
    signer = Path(_required("CONTRACT_SIGNER_FILE")).read_text().strip().removeprefix("0x")
    account = web3.eth.account.from_key(signer)
    if web3.eth.get_balance(account.address) <= 0:
        raise RuntimeError(f"contract signer has no balance: {account.address}")
    authority_abi, authority_bytecode = _artifact("Authority")
    dark_abi, dark_bytecode = _artifact("dARK")
    nonce = web3.eth.get_transaction_count(account.address)

    def deploy(abi: list, bytecode: str, args: list, tx_nonce: int) -> str:
        transaction = web3.eth.contract(abi=abi, bytecode=bytecode).constructor(*args).build_transaction({"from": account.address, "nonce": tx_nonce, "chainId": chain_id, "gas": 3_000_000, "gasPrice": web3.eth.gas_price})
        signed = account.sign_transaction(transaction)
        receipt = web3.eth.wait_for_transaction_receipt(web3.eth.send_raw_transaction(signed.rawTransaction), timeout=180)
        if receipt.status != 1 or not receipt.contractAddress:
            raise RuntimeError("contract deployment transaction failed")
        return receipt.contractAddress

    authority = deploy(authority_abi, authority_bytecode, [], nonce)
    dark = deploy(dark_abi, dark_bytecode, [authority], nonce + 1)
    handoff = {"version": 1, "chain_id": chain_id, "signer": account.address, "contracts": {"authority": authority, "dark": dark}}
    handoff_path.write_text(json.dumps(handoff, indent=2, sort_keys=True) + "\n")
    handoff_path.chmod(0o644)
    _write_runtime_env(runtime_env, handoff)
    print("contracts deployed and handoff written")


if __name__ == "__main__":
    main()
