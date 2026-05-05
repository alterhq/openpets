import AppKit
import CoreGraphics
import Foundation
import ImageIO

public enum OpenPetsPetPreviewRenderer {
    public static func idleImage(from bundleURL: URL, scale: CGFloat = 1) throws -> NSImage {
        try image(for: .idle, frameIndex: 0, from: PetBundle.load(from: bundleURL), scale: scale)
    }

    public static func image(
        for animation: PetAnimation,
        frameIndex: Int = 0,
        from petBundle: PetBundle,
        scale: CGFloat = 1
    ) throws -> NSImage {
        guard
            let source = CGImageSourceCreateWithURL(petBundle.spritesheetURL as CFURL, nil),
            let spritesheet = CGImageSourceCreateImageAtIndex(source, 0, nil)
        else {
            throw OpenPetsError.invalidSpritesheet(petBundle.spritesheetURL)
        }

        let column = min(max(frameIndex, 0), animation.frameCount - 1)
        let rect = CGRect(
            x: column * petBundle.atlas.cellWidth,
            y: animation.row * petBundle.atlas.cellHeight,
            width: petBundle.atlas.cellWidth,
            height: petBundle.atlas.cellHeight
        )
        guard let frame = spritesheet.cropping(to: rect) else {
            throw OpenPetsError.invalidSpritesheet(petBundle.spritesheetURL)
        }

        let imageScale = max(scale, 0.01)
        return NSImage(
            cgImage: frame,
            size: CGSize(
                width: CGFloat(petBundle.atlas.cellWidth) * imageScale,
                height: CGFloat(petBundle.atlas.cellHeight) * imageScale
            )
        )
    }
}
