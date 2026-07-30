require("@nomicfoundation/hardhat-toolbox");
require("dotenv").config();

const GIWA_ORACLE_PRIVATE_KEY = process.env.GIWA_ORACLE_PRIVATE_KEY || "";

/** @type import('hardhat/config').HardhatUserConfig */
module.exports = {
  solidity: {
    version: "0.8.20",
    settings: {
      optimizer: { enabled: true, runs: 200 },
    },
  },
  networks: {
    // GIWA Sepolia — OP-Stack EVM L2 testnet. Standard EIP-1559 gas, no
    // Hedera-style hardcoded gasPrice/gasLimit workaround needed (unlike
    // the old hederaTestnet config this replaces, which required both
    // because the hashio relay couldn't reliably estimate gas).
    giwaSepolia: {
      url: "https://sepolia-rpc.giwa.io/",
      chainId: 91342,
      accounts: GIWA_ORACLE_PRIVATE_KEY ? [GIWA_ORACLE_PRIVATE_KEY] : [],
    },
  },
  paths: {
    sources: "./src",
    artifacts: "./artifacts",
    cache: "./cache",
    tests: "./tests",
  },
};
