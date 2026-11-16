/// Every item of a paged list endpoint: pages of `pageSize` (the API's largest) until
/// the length the server reports (`X-Total-Count`) is reached. `page` fetches one page
/// at `offset` and returns its items with that total.
nonisolated func allPages<Item>(
    pageSize: Int = 1000,
    _ page: (_ limit: Int, _ offset: Int) async throws -> (items: [Item], total: Int)
) async throws -> [Item] {
    var items: [Item] = []
    while true {
        let (batch, total) = try await page(pageSize, items.count)
        items += batch
        if batch.isEmpty || items.count >= total { return items }
    }
}
