// Thin, typed wrappers over validated GraphQL documents. All writes go through client.mutate().
import { loadOperation, assertNoUserErrors } from './client.mjs';

const doc = (name) => loadOperation(name);

export const shopInfo = (client) => client.query(doc('ShopInfo'), {}, { operation: 'ShopInfo' });

/** Iterate all products matching an optional search query. */
export async function* listProducts(client, { query, pageSize = 50 } = {}) {
  let after = null;
  do {
    const data = await client.query(doc('ProductsList'), { first: pageSize, after, query }, { operation: 'ProductsList' });
    yield* data.products.nodes;
    after = data.products.pageInfo.hasNextPage ? data.products.pageInfo.endCursor : null;
  } while (after);
}

export const listThemes = (client) => client.query(doc('ThemesList'), {}, { operation: 'ThemesList' });

async function write(client, name, variables, field, changeId) {
  const data = await client.mutate(doc(name), variables, { operation: name, changeId });
  if (data?.dryRun) return data;
  return assertNoUserErrors(data[field], name);
}

/** Create-or-update a product (idempotent when `identifier` is given, e.g. { handle }). */
export const productSet = (client, input, { identifier, synchronous = true, changeId } = {}) =>
  write(client, 'ProductSet', { input, identifier, synchronous }, 'productSet', changeId);

export const productUpdate = (client, product, { media, changeId } = {}) =>
  write(client, 'ProductUpdate', { product, media }, 'productUpdate', changeId);

export const variantsBulkUpdate = (client, productId, variants, { changeId } = {}) =>
  write(client, 'ProductVariantsBulkUpdate', { productId, variants }, 'productVariantsBulkUpdate', changeId);

export const inventorySetQuantities = (client, input, idempotencyKey, { changeId } = {}) =>
  write(client, 'InventorySetQuantities', { input, idempotencyKey }, 'inventorySetQuantities', changeId);

export const collectionCreate = (client, input, { changeId } = {}) =>
  write(client, 'CollectionCreate', { input }, 'collectionCreate', changeId);

export const metafieldsSet = (client, metafields, { changeId } = {}) => {
  if (metafields.length > 25) throw new Error('metafieldsSet accepts at most 25 metafields per call');
  return write(client, 'MetafieldsSet', { metafields }, 'metafieldsSet', changeId);
};

export const fileCreate = (client, files, { changeId } = {}) =>
  write(client, 'FileCreate', { files }, 'fileCreate', changeId);

export const pageCreate = (client, page, { changeId } = {}) =>
  write(client, 'PageCreate', { page }, 'pageCreate', changeId);

export const webhookSubscriptionCreate = (client, topic, webhookSubscription, { changeId } = {}) =>
  write(client, 'WebhookSubscriptionCreate', { topic, webhookSubscription }, 'webhookSubscriptionCreate', changeId);

/** Iterate orders (no PII selected) e.g. query: "created_at:>=2026-10-01". */
export async function* listOrders(client, { query, pageSize = 100 } = {}) {
  let after = null;
  do {
    const data = await client.query(doc('OrdersForAnalytics'), { first: pageSize, after, query }, { operation: 'OrdersForAnalytics' });
    yield* data.orders.nodes;
    after = data.orders.pageInfo.hasNextPage ? data.orders.pageInfo.endCursor : null;
  } while (after);
}
