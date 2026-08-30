// One-off migration: re-key the price store from {scrip_code}_{date} to
// {symbol}_{date}, and replace the unique (scrip_code,date) index with a unique
// (symbol,date) index.
//
// WHY: NSE scrip codes are NOT unique per symbol (renamed/demerged tickers reuse a
// code, e.g. LT/LTM both = 11483). Keying price docs on scrip_code let distinct
// symbols overwrite each other's bars in a shared _id namespace, corrupting series.
// Identity is the symbol; scrip_code is kept only as a (non-unique) lookup field.
//
// SAFE TO RE-RUN: rebuilds each collection from itself into a temp, then swaps. The
// $group dedups any accidental (symbol,date) duplicates (keeps first). Docs with a
// null/empty symbol are dropped and reported.
//
// Run:  docker exec -i mh-mongo mongosh market_hunt < scripts/migrate_price_id_to_symbol.js
//
// AFTER migration, reload the symbols whose series were corrupted by the old key
// (LT/LTM shared 11483; CANHLIFE shared 11840 with NH and was never stored):
//   python DataLoadManagement.py download-stock LT  --force-refresh
//   python DataLoadManagement.py download-stock LTM --force-refresh
//   python DataLoadManagement.py download-stock CANHLIFE
// then top-up the whole universe (now cheap/incremental):
//   python DataLoadManagement.py download-index NIFTY500

// MUST be run with `mongosh --file <thisfile>` (whole-program parse). Piping via
// stdin runs REPL line-by-line and silently breaks multi-line statements.
const PRICE_RE = /^prices_\d{4}_\d{4}$/;
const dbm = db.getSiblingDB("market_hunt");
const colls = dbm.getCollectionNames().filter(function (c) { return PRICE_RE.test(c); }).sort();

print("=== price _id migration: scrip_code -> symbol ===");
print("collections to migrate (" + colls.length + "): " + JSON.stringify(colls));
let grandBefore = 0, grandAfter = 0;

colls.forEach(function (C) {
  // Hard guard: never touch a non-price collection, even if the filter regressed.
  if (!PRICE_RE.test(C)) { print("SKIP (not a price collection): " + C); return; }
  const before = dbm[C].countDocuments();
  const nullSym = dbm[C].countDocuments({ $or: [{ symbol: null }, { symbol: "" }] });
  const tmp = C + "_mig_tmp";
  dbm[tmp].drop();

  // Rebuild with symbol-keyed _id; $group collapses any duplicate (symbol,date).
  dbm[C].aggregate([
    { $match: { symbol: { $nin: [null, ""] } } },
    { $group: {
        _id: { $concat: ["$symbol", "_", { $dateToString: { format: "%Y%m%d", date: "$date" } }] },
        doc: { $first: "$$ROOT" }
    }},
    { $replaceWith: { $mergeObjects: ["$doc", { _id: "$_id" }] } },
    { $out: tmp }
  ], { allowDiskUse: true });

  const after = dbm[tmp].countDocuments();

  // Swap temp -> live.
  dbm[C].drop();
  dbm[tmp].renameCollection(C);

  // Correct indexes: identity is (symbol,date); scrip_code is a lookup-only field.
  dbm[C].createIndex({ symbol: 1, date: 1 }, { unique: true });
  dbm[C].createIndex({ symbol: 1, date: -1 });
  dbm[C].createIndex({ scrip_code: 1, date: 1 });
  dbm[C].createIndex({ date: -1 });
  dbm[C].createIndex({ year_partition: 1 });

  grandBefore += before; grandAfter += after;
  print(`${C}: before=${before} nullSymbolDropped=${nullSym} after=${after} collapsedDups=${before - nullSym - after}`);
});

// stock_metadata: the old unique index on nse_scrip_code is part of the same bug.
try {
  dbm.stock_metadata.dropIndex("nse_scrip_code_1");
  print("stock_metadata: dropped unique nse_scrip_code_1");
} catch (e) {
  print("stock_metadata: nse_scrip_code_1 drop skipped (" + e.codeName + ")");
}
dbm.stock_metadata.createIndex({ nse_scrip_code: 1 });

print(`=== done: total ${grandBefore} -> ${grandAfter} docs across ${colls.length} collections ===`);
