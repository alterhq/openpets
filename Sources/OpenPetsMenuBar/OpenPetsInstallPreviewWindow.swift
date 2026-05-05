import AppKit
import OpenPetsCore
import SwiftUI

@MainActor
final class OpenPetsInstallPreviewWindowController: NSWindowController, NSWindowDelegate {
    enum Action {
        case install
        case cancel
    }

    private let onAction: (Action) -> Void
    private let state = OpenPetsInstallPreviewState()
    private var didChooseAction = false

    init(preparedInstall: OpenPetsPreparedInstall, onAction: @escaping (Action) -> Void) {
        self.onAction = onAction
        let window = NSWindow(
            contentRect: CGRect(x: 0, y: 0, width: 460, height: 340),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        window.title = "Install \(preparedInstall.displayName)"
        window.isReleasedWhenClosed = false
        window.center()

        super.init(window: window)

        let previewImage = try? OpenPetsPetPreviewRenderer.idleImage(from: preparedInstall.bundleURL, scale: 1)
        window.contentView = NSHostingView(rootView: OpenPetsInstallPreviewView(
            state: state,
            preparedInstall: preparedInstall,
            previewImage: previewImage,
            onInstall: { [weak self] in
                self?.choose(.install)
            },
            onCancel: { [weak self] in
                self?.choose(.cancel)
            }
        ))
        window.delegate = self
    }

    required init?(coder: NSCoder) {
        nil
    }

    func setInstalling() {
        state.isInstalling = true
        state.canInstall = false
        state.errorMessage = nil
    }

    func showError(_ message: String) {
        state.isInstalling = false
        state.errorMessage = message
    }

    func finishAndClose() {
        didChooseAction = true
        close()
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        !state.isInstalling
    }

    func windowWillClose(_ notification: Notification) {
        guard !didChooseAction else { return }
        choose(.cancel)
    }

    private func choose(_ action: Action) {
        guard !didChooseAction else { return }
        if action == .install {
            setInstalling()
        } else {
            didChooseAction = true
        }
        onAction(action)
        if action == .cancel {
            close()
        }
    }
}

@MainActor
private final class OpenPetsInstallPreviewState: ObservableObject {
    @Published var isInstalling = false
    @Published var canInstall = true
    @Published var errorMessage: String?
}

private struct OpenPetsInstallPreviewView: View {
    @ObservedObject var state: OpenPetsInstallPreviewState
    let preparedInstall: OpenPetsPreparedInstall
    let previewImage: NSImage?
    let onInstall: () -> Void
    let onCancel: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .top, spacing: 18) {
                preview
                    .frame(width: 148, height: 160)

                VStack(alignment: .leading, spacing: 8) {
                    Text(preparedInstall.displayName)
                        .font(.title2.weight(.semibold))
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(preparedInstall.petID)
                        .font(.callout.monospaced())
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)

                    if !preparedInstall.description.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        Text(preparedInstall.description)
                            .font(.body)
                            .foregroundStyle(.secondary)
                            .lineLimit(4)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.top, 4)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }

            Divider()

            if state.isInstalling {
                VStack(alignment: .leading, spacing: 8) {
                    ProgressView()
                        .progressViewStyle(.linear)
                    Text("Installing and loading pet...")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            } else if let errorMessage = state.errorMessage {
                Text(errorMessage)
                    .font(.callout)
                    .foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }

            HStack(spacing: 10) {
                Spacer()
                Button("Cancel", action: onCancel)
                    .keyboardShortcut(.cancelAction)
                    .disabled(state.isInstalling)
                Button("Install", action: onInstall)
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(.borderedProminent)
                    .disabled(state.isInstalling || !state.canInstall)
            }
        }
        .padding(22)
        .frame(width: 460)
        .background(Color(nsColor: .windowBackgroundColor))
    }

    @ViewBuilder
    private var preview: some View {
        if let previewImage {
            Image(nsImage: previewImage)
                .interpolation(.none)
                .resizable()
                .scaledToFit()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .accessibilityLabel(Text(preparedInstall.displayName))
        } else {
            ZStack {
                RoundedRectangle(cornerRadius: 8)
                    .fill(Color(nsColor: .controlBackgroundColor))
                Image(systemName: "pawprint.fill")
                    .font(.system(size: 44, weight: .regular))
                    .foregroundStyle(.secondary)
            }
            .accessibilityLabel(Text("Pet preview unavailable"))
        }
    }
}
