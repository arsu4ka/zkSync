from web3 import Web3


class Depositor:
    def __init__(
    self,
    private_key: str,
    rpc_chain: str,
    ) -> None:
        self.web3 = Web3(Web3.HTTPProvider(rpc_chain))
        
    def deposit_eth_to_zksync(self, sender_address: str, eth_amount: float):
        pass
    