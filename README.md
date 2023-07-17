# Automated transactions for zksync blockchain
<!-- Main classes are placed in *modules* folder and can be imported as following:
```python
from modules import Depositor, MintBrdige, Staker, Swapper
```
Each class and its methods have *docString* documentation, so it should be easy for you to understand the arguments each of them accept and function that they serve
# More detailed info about each of the modules: -->
## Depositor
### **deposit_eth_to_zksync**
Works well. Swaps given amount of ETH from Ethereum blockchain to zksync blockchain.
### **deposit_arbitrum_usdc_to_zksync**
Works well. Swaps given amount of USDC from Arbitrum blockchain to zksync blockchain.

## MintBridge
### **mint**
Works well for Arbitrum and Polygon blockchains. Mints NFT.
### **bridge**
Called by **mint** method, you don't need to call it specifically.

## Staker
### **sync_swap**
Works well for all pools. Adds given amount of token1 and token2 to the given pool on [*sync_swap*](https://syncswap.xyz/pools).
### **kyber_swap**
Works well for all pools. Adds given amount of token1 and relative amount of token2 (calculates by itself) to the given pool on [*kyber_swap*](https://kyberswap.com/pools/zksync?tab=classic)

## Swapper
### **mute_swap**
Only works if
```python
token1_symbol == "eth" or token2_symbol == "eth"
```
Swaps given amount of token1 to token2 on [*mute*](https://app.mute.io/swap)

### **inch_swap**
Works well for all tokens. Swaps given amount of token1 to token2 on [*1inch*](https://app.1inch.io/#/42161/simple/swap/ETH)

### **sync_swap**
Works well for all tokens. Swaps given amount of token1 to token2 on [*sync_swap*](https://syncswap.xyz/).
