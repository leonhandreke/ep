import SwiftUI

/// Lays out its subviews side by side like the CSS grid
/// `grid-template-columns: minmax(min₁, f₁fr) minmax(min₂, f₂fr) …; gap: g` the design uses for
/// the lists, the detail panels and the player bar. Rows that use the same spec at the
/// same width line up, so each detail panel sits under its column.
nonisolated struct ColumnGrid: Layout {
    var spec: ColumnSpec

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? spec.minimumWidth
        let height =
            proposal.height
            ?? subviews.map { $0.sizeThatFits(.init(width: nil, height: nil)).height }.max() ?? 0
        return CGSize(width: max(width, spec.minimumWidth), height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let widths = Self.widths(total: bounds.width, spec: spec)
        var x = bounds.minX
        for (subview, width) in zip(subviews, widths) {
            subview.place(
                at: CGPoint(x: x, y: bounds.minY),
                proposal: ProposedViewSize(width: width, height: bounds.height)
            )
            x += width + spec.gap
        }
    }

    /// Column widths for a total width: free space is shared by fraction; a column whose
    /// share is below its minimum gets the minimum and the rest is shared again (the CSS
    /// "find the size of an fr" algorithm), after the gaps. Below the sum of the minimums,
    /// the minimums.
    static func widths(total: CGFloat, spec: ColumnSpec) -> [CGFloat] {
        let total = total - spec.gap * CGFloat(max(spec.fractions.count - 1, 0))
        var fixed = Set<Int>()
        while true {
            let free = total - fixed.reduce(0) { $0 + spec.minimums[$1] }
            let flex = spec.fractions.indices.filter { !fixed.contains($0) }
            let fractionSum = flex.reduce(0) { $0 + spec.fractions[$1] }
            guard fractionSum > 0, free > 0 else { return spec.minimums }
            let unit = free / fractionSum
            let tooSmall = flex.filter { spec.fractions[$0] * unit < spec.minimums[$0] }
            if tooSmall.isEmpty {
                return spec.fractions.indices.map {
                    fixed.contains($0) ? spec.minimums[$0] : spec.fractions[$0] * unit
                }
            }
            fixed.formUnion(tooSmall)
        }
    }
}
