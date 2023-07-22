import traceback

import utils
from web3 import Web3
from loguru import logger
import asyncio
import config as cnf


class BalanceCheckerDecorator:
    def __init__(self, obj, token_ca: str):
        self.token_ca = token_ca
        self.obj = obj

    @staticmethod
    def traceback_to_file(exc: str, wallet: str):
        with open("errors.txt", "a") as f:
            f.write(f"\n{exc}{wallet}\n")

    def __call__(self, async_function):
        async def execute_and_return(amount_to_swap = None):
            if self.token_ca == "0x000000000000000000000000000000000000800A":
                async def balance() -> float:
                    amount_wei = self.obj.web3_zksync.eth.get_balance(Web3.to_checksum_address(self.obj.address))
                    return await utils.wei_to_amount(self.obj.web3_zksync, amount_wei, cnf.tokens["ETH"])
            else:
                async def balance() -> float:
                    return await utils.get_wallet_balance(self.obj.web3_zksync, self.obj.address, self.token_ca)

            bal = await balance()

            try:
                if amount_to_swap is None:
                    await async_function()
                else:
                    await async_function(amount_to_swap)
            except Exception as e:
                BalanceCheckerDecorator.traceback_to_file(traceback.format_exc(), self.obj.address)
                logger.error(f"{e} | {self.obj.address}")
                return None

            i = 1
            while bal == await balance():
                if i > 10:
                    logger.error(f"Waiting amount exceeded, shutting down {self.obj.address}.")
                    return None
                logger.info("Sleeping 5 seconds, until balance updates")
                await asyncio.sleep(5)
                i += 1
            logger.success("Balance updated")
            return await balance()

        return execute_and_return
