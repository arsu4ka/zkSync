from web3.contract import Contract
from eth_typing import HexStr
import asyncio
import random
import json
from hexbytes import HexBytes
from loguru import logger
from web3 import Web3
import os
from pathlib import Path

ABI_FOLDER = Path(__file__).resolve().parent


def get_wallet_address_from_private_key(web3: Web3, private_key: str) -> str:
    return web3.eth.account.from_key(private_key).address


async def get_nft_id(web3: Web3, tx_hash: str) -> int:
    logs = web3.eth.get_transaction_receipt(HexBytes(tx_hash)).logs

    for log in logs:
        if 'topics' in log and len(log['topics']) > 3:
            topic = log['topics'][3]
            if isinstance(topic, HexBytes):
                nft_id = int(topic.hex(), 16)
                return nft_id


async def setup_tokens_addresses(token1_symbol: str, token2_symbol: str, tokens: dict[str, str]) \
        -> tuple[str, str] | None:
    token1_symbol, token2_symbol = token1_symbol.upper(), token2_symbol.upper()
    if not (token1_symbol in tokens and token2_symbol in tokens):
        logger.error(f"No addresses found for such tokens")
        return None
    token1_address, token2_address = tokens[token1_symbol], tokens[token2_symbol]
    return token1_address, token2_address


async def get_token_decimals(web3: Web3, token_ca: str) -> int:
    try:
        token_contract = await get_token_contract(web3, token_ca)
        decimals = token_contract.functions.decimals().call()
        return decimals

    except Exception as ex:
        logger.error(f'Something went wrong | {ex}')


async def amount_to_wei(web3: Web3, amount: float, token_ca: str) -> int:
    token_decimals = await get_token_decimals(web3, token_ca)
    return int(amount * (10 ** token_decimals))


async def wei_to_amount(web3: Web3, wei_amount: int, token_ca: str) -> float:
    token_decimals = await get_token_decimals(web3, token_ca)
    return wei_amount / (10 ** token_decimals)


async def load_abi(name: str) -> str:
    file_name = os.path.join(ABI_FOLDER, f'abis/{name}.json')
    with open(file_name) as f:
        abi: str = json.load(f)
    return abi


async def get_wallet_balance(web3: Web3, wallet_address: str, token_ca: str) -> float:
    if token_ca != "0x5AEa5775959fBC2557Cc8789bC1bf90A239D9a91":
        wallet_address = web3.to_checksum_address(wallet_address)
        token_ca = web3.to_checksum_address(token_ca)
        token_contract = await get_token_contract(web3, token_ca)
        balance_wei = token_contract.functions.balanceOf(wallet_address).call()
    else:
        balance_wei = web3.eth.get_balance(Web3.to_checksum_address(wallet_address))
    token_decimals = await get_token_decimals(web3, token_ca)

    return balance_wei / (10 ** token_decimals)


async def get_contract(address, web3, abi_name) -> Contract:
    address = web3.to_checksum_address(address)
    return web3.eth.contract(address=address, abi=await load_abi(abi_name))


async def get_token_contract(web3: Web3, token_ca: str) -> Contract:
    contract = await get_contract(token_ca, web3, "erc20")
    return contract


async def approve_token(
        amount: float,
        private_key: str,
        chain: str,
        from_token_address: str,
        from_token_symbol: str,
        spender: str,
        web3: Web3
        ) -> HexStr:
    try:
        spender = web3.to_checksum_address(spender)
        address_wallet = get_wallet_address_from_private_key(web3, private_key)
        contract = await get_token_contract(web3, from_token_address)
        allowance_amount = await check_allowance(web3, from_token_address, address_wallet, spender)
        diff = amount - allowance_amount

        if diff > 0:
            tx = contract.functions.approve(
                spender,
                100000000000000000000000000000000000000000000000000000000000000000000000000000
            ).build_transaction(
                {
                    'chainId': web3.eth.chain_id,
                    'from': address_wallet,
                    'nonce': web3.eth.get_transaction_count(web3.to_checksum_address(address_wallet)),
                    'gasPrice': 0,
                    'gas': 0,
                    'value': 0
                }
            )
            if chain == 'bsc':
                tx['gasPrice'] = random.randint(1000000000, 1050000000)
            else:
                gas_price = await add_gas_price(web3)
                tx['gasPrice'] = gas_price
            tx['gas'] = await add_gas_limit(web3, tx)

            signed_tx = web3.eth.account.sign_transaction(tx, private_key=private_key)
            raw_tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
            tx_receipt = web3.eth.wait_for_transaction_receipt(raw_tx_hash)
            while tx_receipt is None:
                await asyncio.sleep(1)
                tx_receipt = web3.eth.get_transaction_receipt(raw_tx_hash)
            tx_hash = web3.to_hex(raw_tx_hash)
            logger.info(f'Infinity {from_token_symbol} approved for {address_wallet} wallet | Tx '
                        f'hash: {tx_hash}')
            await asyncio.sleep(5)
            return tx_hash

    except Exception as ex:
        logger.error(f'Something went wrong | {ex}')


async def check_allowance(web3: Web3, from_token_address: str, address_wallet: str, spender: str) -> float:
    try:
        contract = await get_token_contract(web3, from_token_address)
        amount_approved = contract.functions.allowance(address_wallet, spender).call()
        return amount_approved

    except Exception as ex:
        logger.error(f'Something went wrong | {ex}')


async def add_gas_price(web3: Web3) -> int:
    try:
        gas_price = web3.eth.gas_price
        gas_price = int(gas_price * random.uniform(1.01, 1.02))
        return gas_price
    except Exception as ex:
        logger.error(f'Something went wrong | {ex}')


async def add_gas_limit(web3: Web3, tx: dict) -> int:
    tx['value'] = 0
    gas_limit = web3.eth.estimate_gas(tx)

    return gas_limit
