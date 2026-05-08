import Image from "next/image";
import { useCatTheme } from "./catmagui";

type BrandLogoProps = {
    maxWidth?: number;
    marginBottom?: number;
};

export default function BrandLogo({
    maxWidth = 256,
    marginBottom = 0,
}: BrandLogoProps) {

    const { isDark } = useCatTheme();
    const logoSrc = isDark
        ? "/brand/lastwrite_darkmode.png"
        : "/brand/lastwrite_lightmode.png";

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
                    src={logoSrc}
                    alt="Last Write logo"
                    width={2048}
                    height={2048}
                    priority
                    style={{ width: "100%", height: "auto", display: "block" }}
                />
            </div>
        </div>
    );
}