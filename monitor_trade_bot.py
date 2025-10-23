#!/usr/bin/env python3
# coding: utf-8

import os
import time
import json
import asyncio
import logging
from decimal import Decimal
from typing import Optional, Tuple, List, Set

import requests
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from web3 import Web3
from web3.middleware import geth_poa_middleware

# ===================== LOGGING =====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("bsc-bot")

# ===================== SETTINGS =====================
# Dexscreener monitoring
URL = "https://dexscreener.com/bsc/pancakeswap?rankBy=pairAge&order=asc"
SELECTOR = "a.ds-dex-table-row.ds-dex-table-row-new"
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "10"))
LIQUIDITY_USD_THRESHOLD = float(os.getenv("LIQUIDITY_USD_THRESHOLD", "30000"))
HONEYPOT_API_URL = "https://api.honeypot.is/v2/IsHoneypot"

# Chain / Wallet
RPC_URL = os.getenv("BSC_RPC_URL", "https://bsc-dataseed.binance.org/")
MY_ADDRESS = os.getenv("BSC_ADDRESS", "")
PRIVATE_KEY = os.getenv("BSC_PRIVATE_KEY", "")
CHAIN_ID = int(os.getenv("BSC_CHAIN_ID", "56"))  # BSC mainnet

# PancakeSwap contracts
PANCAKE_ROUTER = Web3.to_checksum_address("0x10ED43C718714eb63d5aA57B78B54704E256024E")
PANCAKE_FACTORY = Web3.to_checksum_address("0xca143ce32fe78f1f7019d7d551a6402fc5350c73")
WBNB = Web3.to_checksum_address("0xBB4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c")

# Trading config
BNB_TO_SPEND = Web3.to_wei(Decimal(os.getenv("BNB_TO_SPEND", "0.001")), "ether")
SLIPPAGE = Decimal(os.getenv("SLIPPAGE", "0.02"))
DEADLINE_SECONDS = int(os.getenv("DEADLINE_SECONDS", "120"))
GAS_LIMIT_BUY = int(os.getenv("GAS_LIMIT_BUY", "300000"))
GAS_LIMIT_SELL = int(os.getenv("GAS_LIMIT_SELL", "800000"))
WAIT_BEFORE_SELL_SECONDS = int(os.getenv("WAIT_BEFORE_SELL_SECONDS", "2"))

# ===================== ABI =====================
ROUTER_ABI = json.loads(
    """
[
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
]
"""
)

ERC20_ABI = json.loads(
    """
[
  {"constant":true,"inputs":[{"name":"owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
  {"constant":false,"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
  {"constant":true,"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"},
  {"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"}
]
"""
)

FACTORY_ABI = json.loads(
    """
[
  {"constant":true,"inputs":[{"name":"tokenA","type":"address"},{"name":"tokenB","type":"address"}],
   "name":"getPair","outputs":[{"name":"","type":"address"}],"type":"function"}
]
"""
)

# ===================== Web3 =====================
w3 = Web3(Web3.HTTPProvider(RPC_URL))
w3.middleware_onion.inject(geth_poa_middleware, layer=0)
router = w3.eth.contract(address=PANCAKE_ROUTER, abi=ROUTER_ABI)
factory = w3.eth.contract(address=PANCAKE_FACTORY, abi=FACTORY_ABI)

# ===================== HELPERS =====================
def parse_liquidity(liq_text: str) -> float:
    try:
        normalized = liq_text.replace("$", "").replace(",", "").strip()
        if "K" in normalized:
            return float(normalized.replace("K", "")) * 1_000
        if "M" in normalized:
            return float(normalized.replace("M", "")) * 1_000_000
        return float(normalized)
    except Exception:
        return 0.0


def fetch_honeypot_data(address: str) -> Tuple[Optional[str], Optional[int], Optional[int], Optional[int], Optional[List[str]]]:
    try:
        response = requests.get(HONEYPOT_API_URL, params={"address": address}, timeout=12)
        response.raise_for_status()
        data = response.json()
        risk = data.get("summary", {}).get("risk", "unknown")
        sim = data.get("simulationResult", {}) or {}
        buy_tax = sim.get("buyTax", 1)
        sell_tax = sim.get("sellTax", 1)
        transfer_tax = sim.get("transferTax", 1)
        flags = data.get("flags") or []
        return risk, buy_tax, sell_tax, transfer_tax, flags
    except Exception as e:
        logger.warning(f"honeypot api failed: {e}")
        return None, None, None, None, None


def simulate_sell(token_address: str) -> bool:
    try:
        token = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)
        decimals = token.functions.decimals().call()
        test_amount = 10 ** int(decimals)
        path = [Web3.to_checksum_address(token_address), WBNB]
        amounts = router.functions.getAmountsOut(test_amount, path).call()
        received_bnb = Decimal(amounts[-1]) / Decimal(10 ** 18)
        return received_bnb > 0
    except Exception:
        return False


def token_on_pancake(token_address: str) -> bool:
    try:
        pair = factory.functions.getPair(Web3.to_checksum_address(token_address), WBNB).call()
        is_present = pair and pair.lower() != "0x0000000000000000000000000000000000000000"
        if not is_present:
            logger.info("❌ Токен НЕ найден на PancakeSwap")
            return False
        logger.info(f"✅ Пара TOKEN/WBNB найдена: {pair}")
        return True
    except Exception as e:
        logger.warning(f"getPair failed: {e}")
        return False


# ===================== TRADING =====================
def buy_token(token_address: str) -> bool:
    if not MY_ADDRESS or not PRIVATE_KEY:
        logger.error("Укажите переменные окружения BSC_ADDRESS и BSC_PRIVATE_KEY")
        return False

    if not token_on_pancake(token_address):
        return False

    balance_bnb = w3.eth.get_balance(MY_ADDRESS)
    logger.info(f"Баланс BNB: {w3.from_wei(balance_bnb, 'ether')}")
    if balance_bnb < BNB_TO_SPEND:
        logger.warning("⚠️ Недостаточно BNB для покупки.")
        return False

    path = [WBNB, Web3.to_checksum_address(token_address)]

    min_tokens = 0
    try:
        amounts = router.functions.getAmountsOut(BNB_TO_SPEND, path).call()
        min_tokens = int(Decimal(amounts[-1]) * (Decimal(1) - SLIPPAGE))
        logger.info(f"🔹 Покупаем. Ожидаемо: {amounts[-1]}, минимум: {min_tokens}")
    except Exception as e:
        logger.warning(f"getAmountsOut (buy) failed, proceeding with min_tokens=0: {e}")

    tx = router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
        min_tokens, path, MY_ADDRESS, int(time.time()) + DEADLINE_SECONDS
    ).build_transaction({
        "from": MY_ADDRESS,
        "value": BNB_TO_SPEND,
        "gas": GAS_LIMIT_BUY,
        "gasPrice": w3.eth.gas_price,
        "nonce": w3.eth.get_transaction_count(MY_ADDRESS),
        "chainId": CHAIN_ID,
    })

    signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    logger.info(f"🚀 Покупка отправлена: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    ok = receipt.status == 1
    logger.info("✅ Покупка выполнена!" if ok else "⚠️ Ошибка при покупке")
    return ok


def sell_all_tokens(token_address: str) -> bool:
    if not MY_ADDRESS or not PRIVATE_KEY:
        logger.error("Укажите переменные окружения BSC_ADDRESS и BSC_PRIVATE_KEY")
        return False

    token = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)
    balance = token.functions.balanceOf(MY_ADDRESS).call()
    if balance == 0:
        logger.warning("⚠️ Баланс токена = 0, нечего продавать.")
        return False

    logger.info(f"🔹 Баланс токена: {balance}")

    allowance = token.functions.allowance(MY_ADDRESS, PANCAKE_ROUTER).call()
    if allowance < balance:
        logger.info("🔸 Делаем approve...")
        approve_tx = token.functions.approve(PANCAKE_ROUTER, balance).build_transaction({
            "from": MY_ADDRESS,
            "nonce": w3.eth.get_transaction_count(MY_ADDRESS),
            "gas": 100000,
            "gasPrice": w3.eth.gas_price,
            "chainId": CHAIN_ID,
        })
        signed = w3.eth.account.sign_transaction(approve_tx, PRIVATE_KEY)
        approve_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        logger.info(f"✅ Approve отправлен: {approve_hash.hex()}")
        w3.eth.wait_for_transaction_receipt(approve_hash)
        time.sleep(3)

    path = [Web3.to_checksum_address(token_address), WBNB]

    min_out = 0
    try:
        amounts = router.functions.getAmountsOut(balance, path).call()
        min_out = int(Decimal(amounts[-1]) * (Decimal(1) - SLIPPAGE))
    except Exception as e:
        logger.warning(f"getAmountsOut (sell) failed, proceeding with min_out=0: {e}")

    func = router.get_function_by_name("swapExactTokensForETHSupportingFeeOnTransferTokens")
    tx_data = func(balance, min_out, path, MY_ADDRESS, int(time.time()) + DEADLINE_SECONDS)._encode_transaction_data()

    tx = {
        "from": MY_ADDRESS,
        "to": PANCAKE_ROUTER,
        "gas": GAS_LIMIT_SELL,
        "data": tx_data,
        "nonce": w3.eth.get_transaction_count(MY_ADDRESS),
        "gasPrice": w3.eth.gas_price,
        "chainId": CHAIN_ID,
    }

    signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    logger.info(f"🚀 Продажа отправлена: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    ok = receipt.status == 1
    logger.info("✅ Продажа выполнена!" if ok else "⚠️ Ошибка при продаже")
    return ok


# ===================== MONITOR + EXECUTE =====================
async def monitor_and_trade() -> None:
    processed: Set[str] = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, как Gecko) Chrome/117 Safari/537.36"
            )
        )
        page = await context.new_page()
        await page.goto(URL, wait_until="domcontentloaded")

        try:
            while True:
                try:
                    await page.wait_for_selector(SELECTOR, timeout=12_000)
                    element = await page.query_selector(SELECTOR)
                    if not element:
                        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
                        continue

                    # Liquidity filter
                    liquidity_element = await element.query_selector(
                        ".ds-dex-table-row-col-liquidity"
                    )
                    liquidity_text = (
                        await liquidity_element.inner_text()
                        if liquidity_element
                        else "0"
                    )
                    liquidity_value = parse_liquidity(liquidity_text)
                    if liquidity_value < LIQUIDITY_USD_THRESHOLD:
                        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
                        continue

                    # Open pair page
                    href = await element.get_attribute("href")
                    if href and href.startswith("/"):
                        href = "https://dexscreener.com" + href

                    if href:
                        await page.goto(href, wait_until="domcontentloaded")
                    else:
                        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
                        continue

                    image_meta = await page.query_selector('meta[property="og:image"]')
                    if not image_meta:
                        await page.goto(URL, wait_until="domcontentloaded")
                        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
                        continue

                    image_url = await image_meta.get_attribute("content")
                    if not image_url or "/bsc/" not in image_url:
                        await page.goto(URL, wait_until="domcontentloaded")
                        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
                        continue

                    try:
                        token_address_raw = image_url.split("/bsc/")[-1].split("?")[0]
                        token_address = Web3.to_checksum_address(token_address_raw)
                    except Exception:
                        await page.goto(URL, wait_until="domcontentloaded")
                        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
                        continue

                    if token_address in processed:
                        await page.goto(URL, wait_until="domcontentloaded")
                        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
                        continue

                    # Safety checks: Honeypot + simulate sell
                    risk, buy_tax, sell_tax, transfer_tax, flags = fetch_honeypot_data(
                        token_address
                    )
                    sell_ok = simulate_sell(token_address)

                    qualifies = (
                        risk == "low"
                        and buy_tax == 0
                        and sell_tax == 0
                        and transfer_tax == 0
                        and not flags
                        and sell_ok
                    )

                    if qualifies:
                        logger.info("\n✅ Подходящий токен найден!")
                        logger.info(f"🔗 {href}")
                        logger.info(f"💧 Ликвидность: {liquidity_text}")
                        logger.info(f"🏷️ Адрес токена: {token_address}")
                        logger.info(
                            f"📌 Risk: {risk}, Taxes: 0/0/0, Flags: нет, Sell OK"
                        )

                        # Execute buy then sell
                        buy_ok = await asyncio.to_thread(buy_token, token_address)
                        if buy_ok:
                            logger.info(
                                f"⏳ Ждём {WAIT_BEFORE_SELL_SECONDS} сек перед продажей..."
                            )
                            await asyncio.sleep(WAIT_BEFORE_SELL_SECONDS)
                            await asyncio.to_thread(sell_all_tokens, token_address)

                        processed.add(token_address)

                    # Return to the list
                    await page.goto(URL, wait_until="domcontentloaded")

                except PWTimeout:
                    pass
                except Exception as e:
                    logger.warning(f"monitor loop error: {e}")

                await asyncio.sleep(CHECK_INTERVAL_SECONDS)
        finally:
            await browser.close()


if __name__ == "__main__":
    logger.info("Запуск мониторинга Dexscreener + авто трейд...")
    asyncio.run(monitor_and_trade())
