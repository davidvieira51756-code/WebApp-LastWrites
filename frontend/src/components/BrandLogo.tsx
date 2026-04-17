import Image from "next/image";

type BrandLogoProps = {
    maxWidth?: number;
    marginBottom?: number;
};

export default function BrandLogo({
    maxWidth = 200,
    marginBottom = 0,
}: BrandLogoProps) {
    return (
        <div
            style={{
                width: "100%",
                display: "flex",
                justifyContent: "center",
                position: "static",
                marginBottom,
            }}
        >
            <div style={{ width: "100%", maxWidth }}>
                <Image
                    src="/brand/LASTWRITE_w.png"
                    alt="Last Write logo"
                    width={1024}
                    height={1024}
                    priority
                    style={{ width: "100%", height: "auto", display: "block" }}
                />
            </div>
        </div>
    );
}