"use strict";

const fs = require("fs");
const path = require("path");
const solc = require("solc");

const sourceRoot = process.env.CONTRACT_SOURCE_DIR || "/src";
const outputRoot = process.env.CONTRACT_OUTPUT_DIR || "/out";

function sourcesUnder(root) {
  const result = {};
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const absolute = path.join(root, entry.name);
    if (entry.isDirectory()) {
      for (const [name, value] of Object.entries(sourcesUnder(absolute))) {
        result[path.join(entry.name, name)] = value;
      }
    } else if (entry.isFile() && entry.name.endsWith(".sol")) {
      result[entry.name] = { content: fs.readFileSync(absolute, "utf8") };
    }
  }
  return result;
}

const sources = sourcesUnder(sourceRoot);
if (Object.keys(sources).length === 0) {
  throw new Error(`no Solidity sources found under ${sourceRoot}`);
}
const result = JSON.parse(solc.compile(JSON.stringify({
  language: "Solidity",
  sources,
  settings: {
    optimizer: { enabled: true, runs: 200 },
    outputSelection: { "*": { "*": ["abi", "evm.bytecode", "evm.deployedBytecode", "metadata"] } },
  },
})));
const errors = (result.errors || []).filter((item) => item.severity === "error");
if (errors.length) {
  throw new Error(errors.map((item) => item.formattedMessage).join("\n"));
}
fs.mkdirSync(outputRoot, { recursive: true });
for (const [sourceName, contracts] of Object.entries(result.contracts || {})) {
  for (const [contractName, contract] of Object.entries(contracts)) {
    if (!(contractName === "Authority" || contractName === "dARK")) continue;
    fs.writeFileSync(path.join(outputRoot, `${contractName}ABI.json`), JSON.stringify(contract.abi, null, 2) + "\n");
    fs.writeFileSync(path.join(outputRoot, `${contractName}Bytecode.txt`), (contract.evm.bytecode.object || "") + "\n");
    console.log(`compiled ${contractName} from ${sourceName}`);
  }
}
for (const name of ["Authority", "dARK"]) {
  for (const suffix of ["ABI.json", "Bytecode.txt"]) {
    if (!fs.existsSync(path.join(outputRoot, `${name}${suffix}`))) {
      throw new Error(`missing required ${name}${suffix}`);
    }
  }
}
