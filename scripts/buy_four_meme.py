#!/usr/bin/env python3
"""
Buy a four.meme token on BNB Chain (PancakeSwap V2) using a local wallet.

Features:
- Swaps native BNB -> token via best of [WBNB -> token], [WBNB -> USDT -> token], [WBNB -> USDC -> token]
- Slippage control, deadline, custom RPC, custom gas price
- Dry-run mode to preview expected output and path

Usage examples:
  python3 scripts/buy_four_meme.py --token 0x... --amount-bnb 0.02 --slippage 10 --rpc https://bsc-dataseed.binance.org \
    --key 0xYOUR_PRIVATE_KEY

  PRIVATE_KEY=0xYOUR_PRIVATE_KEY python3 scripts/buy_four_meme.py --token 0x... --amount-bnb 0.02 --slippage 7

NOTE: Keep your private key safe. Prefer environment variables over CLI args on shared machines.
"""

import argparse
import os
import sys
import time
from decimal import Decimal, getcontext
from typing import List, Optional, Tuple

from web3 import Web3
from web3.contract import Contract
from web3.exceptions import ContractLogicError

# Increase decimal precision for accurate human -> wei conversions
getcontext().prec = 50

# PancakeSwap V2 Router (BNB Chain mainnet)
PANCAKE_ROUTER_V2 = Web3.to_checksum_address("0x10ED43C718714eb63d5aA57B78B54704E256024E")
# Common tokens on BNB Chain to try as routing hops
WBNB = Web3.to_checksum_address("0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c")
USDT = Web3.to_checksum_address("0x55d398326f99059fF775485246999027B3197955")
USDC = Web3.to_checksum_address("0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d")
COMMON_INTERMEDIARIES: List[str] = [USDT, USDC]

# Minimal ABIs
ROUTER_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactETHForTokensSupportingFeeOnTransferTokens",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
        ],
        "name": "getAmountsOut",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function",
    },
]

ERC20_ABI = [
    {
        "constant": False,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Buy token on BNB Chain via PancakeSwap V2 using local wallet")
    parser.add_argument("--rpc", default="https://bsc-dataseed.binance.org", help="BNB Chain RPC URL")
    parser.add_argument("--token", required=True, help="Token address to buy (checksum or hex)")
    parser.add_argument("--amount-bnb", required=True, type=str, help="Amount of native BNB to spend, e.g., 0.02")
    parser.add_argument("--slippage", default="10", type=str, help="Max slippage percent, e.g., 10 = 10%")
    parser.add_argument("--deadline-seconds", default=180, type=int, help="Trade deadline from now in seconds")
    parser.add_argument("--key", default=None, help="Private key hex (0x...), or use env PRIVATE_KEY")
    parser.add_argument("--recipient", default=None, help="Recipient address; defaults to sender")
    parser.add_argument("--gas-price-gwei", default=None, type=str, help="Override gas price in gwei")
    parser.add_argument("--nonce", default=None, type=int, help="Override nonce")
    parser.add_argument("--dry-run", action="store_true", help="Compute path/amounts but do not send tx")
    parser.add_argument("--no-wait", action="store_true", help="Do not wait for receipt after sending")
    return parser.parse_args()


def to_checksum(addr: str) -> str:
    return Web3.to_checksum_address(addr)


def bnb_to_wei(amount_bnb_str: str) -> int:
    amount = Decimal(amount_bnb_str)
    if amount <= 0:
        raise ValueError("Amount must be positive")
    return int((amount * (Decimal(10) ** 18)).to_integral_value())


def connect_web3(rpc: str) -> Web3:
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        raise RuntimeError(f"Failed to connect to RPC: {rpc}")
    chain_id = w3.eth.chain_id
    if chain_id != 56:
        print(f"[WARN] Connected chain_id={chain_id}, expected 56 (BNB Chain mainnet)")
    return w3


def load_router(w3: Web3) -> Contract:
    return w3.eth.contract(address=PANCAKE_ROUTER_V2, abi=ROUTER_ABI)


def load_erc20(w3: Web3, token: str) -> Contract:
    return w3.eth.contract(address=to_checksum(token), abi=ERC20_ABI)


def try_get_amount_out(router: Contract, amount_in_wei: int, path: List[str]) -> Optional[int]:
    try:
        out_amounts = router.functions.getAmountsOut(amount_in_wei, path).call()
        if not out_amounts or len(out_amounts) < 2:
            return None
        return int(out_amounts[-1])
    except (ContractLogicError, ValueError):
        return None


def select_best_path(router: Contract, amount_in_wei: int, token_addr: str) -> Tuple[List[str], int]:
    token = to_checksum(token_addr)
    candidates: List[List[str]] = [[WBNB, token]]
    for mid in COMMON_INTERMEDIARIES:
        candidates.append([WBNB, mid, token])

    best_path: Optional[List[str]] = None
    best_out: int = 0

    for path in candidates:
        out_amount = try_get_amount_out(router, amount_in_wei, path)
        if out_amount is not None and out_amount > best_out:
            best_out = out_amount
            best_path = path

    if not best_path:
        raise RuntimeError("No viable swap path found. Token may be illiquid or not paired.")

    return best_path, best_out


def compute_min_out(expected_out: int, slippage_percent_str: str) -> int:
    sl = Decimal(slippage_percent_str)
    if sl < 0 or sl > 100:
        raise ValueError("Slippage must be between 0 and 100")
    min_out = Decimal(expected_out) * (Decimal(1) - sl / Decimal(100))
    # round down to int
    return int(min_out.to_integral_value(rounding="ROUND_DOWN"))


def derive_sender_from_key(w3: Web3, key_hex: str) -> str:
    acct = w3.eth.account.from_key(key_hex)
    return acct.address


def build_and_send_swap(
    w3: Web3,
    router: Contract,
    sender: str,
    key_hex: str,
    amount_in_wei: int,
    amount_out_min: int,
    path: List[str],
    recipient: str,
    deadline_ts: int,
    gas_price_gwei: Optional[str],
    nonce_override: Optional[int],
    wait: bool,
) -> str:
    # Base tx parameters
    tx_params = {
        "from": sender,
        "value": amount_in_wei,
    }

    # Build function call
    func = router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
        amount_out_min, path, recipient, deadline_ts
    )

    # First build minimal tx for gas estimation
    built = func.build_transaction({"from": sender, "value": amount_in_wei})

    # Estimate gas (fallback to a safe ceiling on failure)
    try:
        estimated_gas = w3.eth.estimate_gas(built)
        gas_limit = int(estimated_gas * 1.2)  # add headroom
    except Exception:
        gas_limit = 500_000

    # Gas price handling (use legacy gasPrice for BNB Chain)
    if gas_price_gwei is not None:
        gas_price = int(Decimal(gas_price_gwei) * (10 ** 9))
    else:
        gas_price = w3.eth.gas_price

    nonce = nonce_override if nonce_override is not None else w3.eth.get_transaction_count(sender)

    tx = func.build_transaction(
        {
            "from": sender,
            "value": amount_in_wei,
            "gas": gas_limit,
            "gasPrice": gas_price,
            "nonce": nonce,
            "chainId": w3.eth.chain_id,
        }
    )

    signed = w3.eth.account.sign_transaction(tx, private_key=key_hex)
    tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
    hex_hash = tx_hash.hex()

    print(f"Sent swap tx: {hex_hash}")

    if wait:
        print("Waiting for receipt...")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
        status = receipt.status
        print(f"Receipt status: {status}, gasUsed={receipt.gasUsed}")
        if status != 1:
            print("[WARN] Transaction failed. Inspect on a BSC explorer.")
    return hex_hash


def main():
    args = parse_args()

    # Resolve private key
    key = args.key or os.environ.get("PRIVATE_KEY")
    if key is None and not args.dry_run:
        print("ERROR: Provide --key or set PRIVATE_KEY env var (use dry-run to preview)")
        sys.exit(2)

    # Connect
    w3 = connect_web3(args.rpc)
    router = load_router(w3)

    token_addr = to_checksum(args.token)
    token = load_erc20(w3, token_addr)

    # Read metadata for display (non-critical)
    try:
        token_symbol = token.functions.symbol().call()
    except Exception:
        token_symbol = "TOKEN"
    try:
        token_decimals = int(token.functions.decimals().call())
    except Exception:
        token_decimals = 18

    amount_in_wei = bnb_to_wei(args.amount_bnb)
    deadline = int(time.time()) + int(args.deadline_seconds)

    # Select best path
    path, expected_out = select_best_path(router, amount_in_wei, token_addr)
    min_out = compute_min_out(expected_out, args.slippage)

    # Pretty print preview
    print("--- Trade Preview ---")
    print(f"RPC: {args.rpc}")
    print(f"Token: {token_addr} ({token_symbol})")
    print(f"Amount In (BNB): {args.amount_bnb}")
    print(f"Expected Out (~{token_symbol}): {expected_out / (10 ** token_decimals):.6f}")
    print(f"Min Out (slippage {args.slippage}%): {min_out / (10 ** token_decimals):.6f}")
    print("Path:")
    for hop in path:
        print(f"  - {hop}")

    if args.dry_run:
        print("Dry run: not sending any transaction.")
        return

    sender = derive_sender_from_key(w3, key)
    recipient = to_checksum(args.recipient) if args.recipient else sender

    print(f"Sender:   {sender}")
    print(f"Recipient:{recipient}")

    try:
        tx_hash = build_and_send_swap(
            w3=w3,
            router=router,
            sender=sender,
            key_hex=key,
            amount_in_wei=amount_in_wei,
            amount_out_min=min_out,
            path=path,
            recipient=recipient,
            deadline_ts=deadline,
            gas_price_gwei=args.gas_price_gwei,
            nonce_override=args.nonce,
            wait=(not args.no_wait),
        )
        print(f"Success! Tx hash: {tx_hash}")
    except Exception as e:
        print(f"ERROR sending transaction: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
