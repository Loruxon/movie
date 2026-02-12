#!/usr/bin/env python3
"""
Refactored PancakeSwap new-pair monitor and trade executor for BSC.
- Async block polling with backoff
- Structured logging
- Environment-driven configuration
- Safe web3 tx building/signing
- Basic guardrails around taxes/liquidity/honeypot

NOTE: Keys and addresses must be set via environment variables or .env file.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Tuple, List

import requests
from web3 import Web3
from web3.contract import Contract
from web3.types import HexBytes

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore


# ===================== Config =====================
@dataclass(frozen=True)
class Config:
    ws_provider: str
    pancake_factory: str
    pancake_router: str
    wbnb: str

    my_address: str
    private_key: str

    chain_id: int
    bnb_to_spend_wei: int
    slippage: Decimal
    deadline_seconds: int

    gas_limit_sell: int
    max_sell_retries: int
    retry_delay_seconds: int
    min_liquidity_usd: float

    honeypot_api_url: str = "https://api.honeypot.is/v2/IsHoneypot"

    @staticmethod
    def load() -> "Config":
        if load_dotenv is not None:
            load_dotenv()  # Load .env if present

        def env_decimal(name: str, default: str) -> Decimal:
            return Decimal(os.getenv(name, default))

        def env_int(name: str, default: str) -> int:
            return int(os.getenv(name, default))

        def env_str(name: str, default: str) -> str:
            return os.getenv(name, default)

        ws_provider = env_str("WS_PROVIDER", "wss://bsc-rpc.publicnode.com")
        pancake_factory = Web3.to_checksum_address(
            env_str("PANCAKE_FACTORY", "0xca143ce32fe78f1f7019d7d551a6402fc5350c73")
        )
        pancake_router = Web3.to_checksum_address(
            env_str("PANCAKE_ROUTER", "0x10ED43C718714eb63d5aA57B78B54704E256024E")
        )
        wbnb = Web3.to_checksum_address(
            env_str("WBNB", "0xBB4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c")
        )

        my_address = Web3.to_checksum_address(env_str("MY_ADDRESS", ""))
        private_key = env_str("PRIVATE_KEY", "")

        if not my_address or not private_key:
            raise SystemExit("MY_ADDRESS and PRIVATE_KEY must be set in environment or .env")

        chain_id = env_int("CHAIN_ID", "56")
        bnb_to_spend_ether = env_decimal("BNB_TO_SPEND", "0.001")
        bnb_to_spend_wei = Web3.to_wei(bnb_to_spend_ether, "ether")
        slippage = env_decimal("SLIPPAGE", "0.02")
        deadline_seconds = env_int("DEADLINE_SECONDS", "120")

        gas_limit_sell = env_int("GAS_LIMIT_SELL", "800000")
        max_sell_retries = env_int("MAX_SELL_RETRIES", "3")
        retry_delay_seconds = env_int("RETRY_DELAY", "2")
        min_liquidity_usd = float(env_decimal("MIN_LIQUIDITY_USD", "50000"))

        return Config(
            ws_provider=ws_provider,
            pancake_factory=pancake_factory,
            pancake_router=pancake_router,
            wbnb=wbnb,
            my_address=my_address,
            private_key=private_key,
            chain_id=chain_id,
            bnb_to_spend_wei=bnb_to_spend_wei,
            slippage=slippage,
            deadline_seconds=deadline_seconds,
            gas_limit_sell=gas_limit_sell,
            max_sell_retries=max_sell_retries,
            retry_delay_seconds=retry_delay_seconds,
            min_liquidity_usd=min_liquidity_usd,
        )


# ===================== ABIs =====================
FACTORY_ABI: List[dict] = json.loads(
    """[
    {
      "anonymous": false,
      "inputs": [
        {"indexed": true, "internalType": "address", "name": "token0", "type": "address"},
        {"indexed": true, "internalType": "address", "name": "token1", "type": "address"},
        {"indexed": false, "internalType": "address", "name": "pair", "type": "address"},
        {"indexed": false, "internalType": "uint256", "name": "", "type": "uint256"}
      ],
      "name": "PairCreated",
      "type": "event"
    }
]"""
)

ROUTER_ABI: List[dict] = json.loads(
    """[
      {"inputs":[{"internalType":"uint256","name":"amountOutMin","type":"uint256"},
                 {"internalType":"address[]","name":"path","type":"address[]"},
                 {"internalType":"address","name":"to","type":"address"},
                 {"internalType":"uint256","name":"deadline","type":"uint256"}],
       "name":"swapExactETHForTokensSupportingFeeOnTransferTokens",
       "outputs":[],"stateMutability":"payable","type":"function"},
      {"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},
                 {"internalType":"address[]","name":"path","type":"address[]"}],
       "name":"getAmountsOut",
       "outputs":[{"internalType":"uint256[]","name":"","type":"uint256[]"}],
       "stateMutability":"view","type":"function"},
      {"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},
                 {"internalType":"uint256","name":"amountOutMin","type":"uint256"},
                 {"internalType":"address[]","name":"path","type":"address[]"},
                 {"internalType":"address","name":"to","type":"address"},
                 {"internalType":"uint256","name":"deadline","type":"uint256"}],
       "name":"swapExactTokensForETHSupportingFeeOnTransferTokens",
       "outputs":[],"stateMutability":"nonpayable","type":"function"}
    ]"""
)

ERC20_ABI: List[dict] = json.loads(
    """[
      {"constant":true,"inputs":[{"name":"owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
      {"constant":false,"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
      {"constant":true,"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"},
      {"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"}
    ]"""
)

PAIR_ABI: List[dict] = json.loads(
    """[
      {"constant":true,"inputs":[],"name":"getReserves","outputs":[
        {"internalType":"uint112","name":"_reserve0","type":"uint112"},
        {"internalType":"uint112","name":"_reserve1","type":"uint112"},
        {"internalType":"uint32","name":"_blockTimestampLast","type":"uint32"}],
       "stateMutability":"view","type":"function"},
      {"constant":true,"inputs":[],"name":"token0","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"},
      {"constant":true,"inputs":[],"name":"token1","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"}
    ]"""
)


# ===================== Logging =====================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("pancake-bot")


# ===================== Web3 Setup =====================
class Web3Ctx:
    def __init__(self, cfg: Config):
        self.w3 = Web3(Web3.LegacyWebSocketProvider(cfg.ws_provider))
        self.router: Contract = self.w3.eth.contract(address=cfg.pancake_router, abi=ROUTER_ABI)
        self.factory: Contract = self.w3.eth.contract(address=cfg.pancake_factory, abi=FACTORY_ABI)

    def check_connection(self) -> None:
        if self.w3.is_connected():
            logger.info("WebSocket connected")
        else:
            raise SystemExit("WebSocket connection failed")


# ===================== Helpers =====================
class TokenCache:
    def __init__(self) -> None:
        self._processed: set[str] = set()

    def has(self, address: str) -> bool:
        return address.lower() in self._processed

    def add(self, address: str) -> None:
        self._processed.add(address.lower())


def fetch_honeypot_data(api_url: str, address: str) -> Tuple[Optional[str], Optional[float], Optional[float], Optional[float], Optional[List[str]]]:
    try:
        response = requests.get(api_url, params={"address": address}, timeout=10)
        response.raise_for_status()
        data = response.json()
        risk = data.get("summary", {}).get("risk", "unknown")
        sim = data.get("simulationResult", {}) or {}
        buy_tax = float(sim.get("buyTax", 1)) if sim.get("buyTax") is not None else None
        sell_tax = float(sim.get("sellTax", 1)) if sim.get("sellTax") is not None else None
        transfer_tax = float(sim.get("transferTax", 1)) if sim.get("transferTax") is not None else None
        flags = data.get("flags", []) or []
        return risk, buy_tax, sell_tax, transfer_tax, flags
    except Exception as exc:
        logger.warning("honeypot api error: %s", exc)
        return None, None, None, None, None


def simulate_sell(w3: Web3, router: Contract, token_address: str, wbnb: str) -> bool:
    try:
        token = w3.eth.contract(address=token_address, abi=ERC20_ABI)
        decimals = token.functions.decimals().call()
        test_amount = 10 ** int(decimals)
        path = [token_address, wbnb]
        amounts = router.functions.getAmountsOut(test_amount, path).call()
        received_bnb = Decimal(amounts[-1]) / Decimal(10**18)
        return received_bnb > 0
    except Exception as exc:
        logger.debug("simulate_sell failed: %s", exc)
        return False


def get_pair_liquidity_usd(w3: Web3, pair_address: str, wbnb: str) -> float:
    try:
        pair = w3.eth.contract(address=pair_address, abi=PAIR_ABI)
        reserves = pair.functions.getReserves().call()
        token0 = pair.functions.token0().call()
        token1 = pair.functions.token1().call()
        if token0.lower() == wbnb.lower():
            bnb_reserve = reserves[0] / 1e18
        elif token1.lower() == wbnb.lower():
            bnb_reserve = reserves[1] / 1e18
        else:
            return 0.0
        price_resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "binancecoin", "vs_currencies": "usd"},
            timeout=10,
        )
        price_resp.raise_for_status()
        price_bnb = float(price_resp.json()["binancecoin"]["usd"])  # type: ignore[index]
        return float(bnb_reserve * price_bnb)
    except Exception as exc:
        logger.debug("liquidity fetch failed: %s", exc)
        return 0.0


# ===================== Trading =====================
class Trader:
    def __init__(self, cfg: Config, w3ctx: Web3Ctx) -> None:
        self.cfg = cfg
        self.w3 = w3ctx.w3
        self.router = w3ctx.router

    def _build_common(self) -> dict:
        return {
            "from": self.cfg.my_address,
            "gasPrice": self.w3.eth.gas_price,
            "chainId": self.cfg.chain_id,
        }

    def buy_token(self, token_address: str) -> bool:
        try:
            path = [self.cfg.wbnb, token_address]
            amounts = self.router.functions.getAmountsOut(self.cfg.bnb_to_spend_wei, path).call()
            min_tokens = int(int(amounts[-1]) * (1 - float(self.cfg.slippage)))
            tx = self.router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
                min_tokens,
                path,
                self.cfg.my_address,
                int(time.time()) + self.cfg.deadline_seconds,
            ).build_transaction(
                {
                    **self._build_common(),
                    "value": self.cfg.bnb_to_spend_wei,
                    "gas": 300000,
                    "nonce": self.w3.eth.get_transaction_count(self.cfg.my_address),
                }
            )
            signed = self.w3.eth.account.sign_transaction(tx, self.cfg.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
            logger.info("buy tx sent: %s", tx_hash.hex())
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
            return receipt.status == 1
        except Exception as exc:
            logger.error("buy failed: %s", exc)
            return False

    def sell_all(self, token_address: str) -> bool:
        for attempt in range(self.cfg.max_sell_retries + 1):
            try:
                token = self.w3.eth.contract(address=token_address, abi=ERC20_ABI)
                balance = int(token.functions.balanceOf(self.cfg.my_address).call())
                if balance == 0:
                    return True
                allowance = int(token.functions.allowance(self.cfg.my_address, self.cfg.pancake_router).call())
                if allowance < balance:
                    approve_tx = token.functions.approve(self.cfg.pancake_router, balance).build_transaction(
                        {
                            **self._build_common(),
                            "gas": 100000,
                            "nonce": self.w3.eth.get_transaction_count(self.cfg.my_address),
                        }
                    )
                    signed_appr = self.w3.eth.account.sign_transaction(approve_tx, self.cfg.private_key)
                    appr_hash = self.w3.eth.send_raw_transaction(signed_appr.rawTransaction)
                    logger.info("approve tx sent: %s", appr_hash.hex())
                    self.w3.eth.wait_for_transaction_receipt(appr_hash)
                    time.sleep(2)

                path = [token_address, self.cfg.wbnb]
                amounts = self.router.functions.getAmountsOut(balance, path).call()
                min_out = int(int(amounts[-1]) * (1 - float(self.cfg.slippage)))
                func = self.router.get_function_by_name(
                    "swapExactTokensForETHSupportingFeeOnTransferTokens"
                )
                tx_data: HexBytes = func(
                    balance,
                    min_out,
                    path,
                    self.cfg.my_address,
                    int(time.time()) + self.cfg.deadline_seconds,
                )._encode_transaction_data()
                tx = {
                    **self._build_common(),
                    "to": self.cfg.pancake_router,
                    "gas": self.cfg.gas_limit_sell,
                    "data": tx_data,
                    "nonce": self.w3.eth.get_transaction_count(self.cfg.my_address),
                }
                signed = self.w3.eth.account.sign_transaction(tx, self.cfg.private_key)
                tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
                logger.info("sell tx sent: %s", tx_hash.hex())
                receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
                if receipt.status == 1:
                    return True
            except Exception as exc:
                logger.warning("sell failed (attempt %s): %s", attempt + 1, exc)
                time.sleep(self.cfg.retry_delay_seconds)
        return False


# ===================== Listener =====================
class PairListener:
    def __init__(self, cfg: Config, w3ctx: Web3Ctx, trader: Trader) -> None:
        self.cfg = cfg
        self.w3 = w3ctx.w3
        self.factory = w3ctx.factory
        self.trader = trader
        self.token_cache = TokenCache()

    async def run(self) -> None:
        last_block = self.w3.eth.block_number
        event = self.factory.events.PairCreated
        logger.info("listening for new PancakeSwap pairs ...")

        while True:
            try:
                current_block = self.w3.eth.block_number
                if current_block > last_block:
                    logs = event.get_logs(from_block=last_block + 1, to_block=current_block)
                    for log in logs:
                        token0 = log["args"]["token0"]
                        token1 = log["args"]["token1"]
                        pair = log["args"]["pair"]

                        token_address = None
                        if token0.lower() == self.cfg.wbnb.lower():
                            token_address = token1
                        elif token1.lower() == self.cfg.wbnb.lower():
                            token_address = token0
                        else:
                            continue

                        if self.token_cache.has(token_address):
                            continue

                        liquidity_usd = get_pair_liquidity_usd(self.w3, pair, self.cfg.wbnb)
                        if liquidity_usd < self.cfg.min_liquidity_usd:
                            logger.info(
                                "skip token %s pair %s: liquidity %.2f < %.2f",
                                token_address,
                                pair,
                                liquidity_usd,
                                self.cfg.min_liquidity_usd,
                            )
                            self.token_cache.add(token_address)
                            continue

                        self.token_cache.add(token_address)
                        logger.info(
                            "new token: %s | pair: %s | liquidity_usd: %.2f",
                            token_address,
                            pair,
                            liquidity_usd,
                        )

                        risk, buy_tax, sell_tax, transfer_tax, flags = fetch_honeypot_data(
                            self.cfg.honeypot_api_url, token_address
                        )
                        sell_ok = simulate_sell(self.w3, self.trader.router, token_address, self.cfg.wbnb)
                        logger.info(
                            "analysis: risk=%s buyTax=%s sellTax=%s transferTax=%s sellPossible=%s flags=%s",
                            risk,
                            buy_tax,
                            sell_tax,
                            transfer_tax,
                            sell_ok,
                            flags,
                        )

                        if (
                            risk == "low"
                            and (buy_tax or 0) == 0
                            and (sell_tax or 0) == 0
                            and (transfer_tax or 0) == 0
                            and (not flags)
                            and sell_ok
                        ):
                            logger.info("eligible token -> buying ...")
                            if self.trader.buy_token(token_address):
                                await asyncio.sleep(5)
                                self.trader.sell_all(token_address)
                last_block = current_block
            except Exception as exc:
                logger.warning("block handling error: %s", exc)
            await asyncio.sleep(1)


async def main() -> None:
    cfg = Config.load()
    w3ctx = Web3Ctx(cfg)
    w3ctx.check_connection()
    trader = Trader(cfg, w3ctx)
    listener = PairListener(cfg, w3ctx, trader)
    await listener.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("terminated by user")
