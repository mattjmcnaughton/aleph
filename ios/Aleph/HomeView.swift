import SwiftUI

/// The "Your paths" home screen (docs/CONTEXT.md: Switcher). A static stub.
struct HomeView: View {
    var paths: [SamplePath] = SamplePath.samples

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Your paths")
                        .font(.title2.weight(.semibold))
                        .accessibilityIdentifier("your-paths-heading")

                    VStack(spacing: 12) {
                        ForEach(paths) { path in
                            PathRow(path: path)
                        }
                    }

                    Button {
                        // Intentionally does nothing in the proof of concept.
                    } label: {
                        Label("New path", systemImage: "plus")
                            .font(.headline)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 6)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.teal)
                    .accessibilityIdentifier("new-path-button")
                }
                .padding(16)
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("aleph")
        }
    }
}

struct PathRow: View {
    let path: SamplePath

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text(path.title)
                    .font(.headline)
                    .foregroundStyle(.primary)
                Spacer(minLength: 8)
                Text("\(path.lessonsComplete)/\(path.lessonCount)")
                    .font(.subheadline.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            Text(path.level.rawValue)
                .font(.caption.weight(.medium))
                .padding(.horizontal, 8)
                .padding(.vertical, 3)
                .background(Color.teal.opacity(0.15), in: Capsule())
                .foregroundStyle(.teal)
            ProgressView(value: path.progress)
                .tint(.teal)
        }
        .padding(14)
        .background(Color(.secondarySystemGroupedBackground),
                    in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("path-row-\(path.id)")
    }
}

#Preview("Light") { HomeView() }
#Preview("Dark") { HomeView().preferredColorScheme(.dark) }
