import SwiftUI

/// The player bar (§3.8): 64 pt, `--bar`, top border, on the 1fr/2fr/1fr grid. Shows the
/// "nothing playing" state (§4.4) until the player (stage 4) exists.
struct PlayerBar: View {
    var body: some View {
        ColumnGrid(spec: .playerBar) {
            HStack(spacing: 10) {
                RoundedRectangle(cornerRadius: Radius.cover)
                    .strokeBorder(Palette.text3, style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
                    .frame(width: Metrics.cover, height: Metrics.cover)
                    .accessibilityHidden(true)
                Text("Double-click or press Return on a track to play.")
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.text3)
                    .lineLimit(2)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
            Color.clear
            Color.clear
        }
        .padding(.horizontal, 16)
        .frame(height: Metrics.playerBarHeight)
        .background(Palette.bar)
        .overlay(alignment: .top) { HairlineDivider() }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Player")
        .accessibilityIdentifier("player.bar")
    }
}
