import os
import click
import struct
from rich import print
from rich.progress import track
from typing import Tuple, BinaryIO

BLOCK_SIZE = 512
DISK_INODE_SIZE = 128
DIR_ENTRY_SIZE = 32

SUPER_BLOCK_FMT = "<6I488x"
DISK_INODE_FMT = "<B3xI27IIII"
INDIRECT_BLOCK_FMT = "<128I"
DIR_ENTRY_FMT = "<28sI"

MAGIC_NUMBER = 0x3B80_0001


class Inode:
    def __init__(
        self,
        file_type: int,
        size: int,
        direct: Tuple[int, ...],
        indirect1: int,
        indirect2: int,
        indirect3: int,
    ):
        self.file_type = file_type
        self.size = size
        self.direct = direct
        self.indirect1 = indirect1
        self.indirect2 = indirect2
        self.indirect3 = indirect3


def read_indirect_block(
    image: BinaryIO, indirect_block_index: int, level: int
) -> bytes:
    data = b""

    image.seek(indirect_block_index * BLOCK_SIZE)
    indirect_block_data = image.read(BLOCK_SIZE)
    indirect_block_indices = struct.unpack(INDIRECT_BLOCK_FMT, indirect_block_data)

    for index in indirect_block_indices:
        if index == 0:
            break
        if level == 1:
            image.seek(index * BLOCK_SIZE)
            data += image.read(BLOCK_SIZE)
        else:
            data += read_indirect_block(image, index, level - 1)

    return data


def extract_inode_data(image: BinaryIO, inode: Inode) -> bytes:
    data = b""

    for block_index in inode.direct:
        if block_index == 0:
            break
        image.seek(block_index * BLOCK_SIZE)
        data += image.read(BLOCK_SIZE)
    if inode.indirect1 != 0:
        data += read_indirect_block(image, inode.indirect1, 1)
    if inode.indirect2 != 0:
        data += read_indirect_block(image, inode.indirect2, 2)
    if inode.indirect3 != 0:
        data += read_indirect_block(image, inode.indirect3, 3)

    return data[: inode.size]


def extract_directory(image: BinaryIO, inode_index: int, output_path: str):
    image.seek(BLOCK_SIZE * 2 + DISK_INODE_SIZE * inode_index)
    inode_data = image.read(DISK_INODE_SIZE)
    unpacked_data = struct.unpack(DISK_INODE_FMT, inode_data)

    inode = Inode(
        file_type=unpacked_data[0],
        size=unpacked_data[1],
        direct=unpacked_data[2:29],
        indirect1=unpacked_data[29],
        indirect2=unpacked_data[30],
        indirect3=unpacked_data[31],
    )

    if inode.file_type == 1:  # Directory
        if not os.path.exists(output_path):
            os.mkdir(output_path)
        directory_data = extract_inode_data(image, inode)
        for offset in range(DIR_ENTRY_SIZE * 2, inode.size, DIR_ENTRY_SIZE):
            dir_entry = struct.unpack(
                DIR_ENTRY_FMT, directory_data[offset : offset + DIR_ENTRY_SIZE]
            )
            name, child_inode_index = dir_entry
            name = name.split(b"\x00", 1)[0].decode()
            extract_directory(image, child_inode_index, os.path.join(output_path, name))
    elif inode.file_type == 0:  # File
        file_data = extract_inode_data(image, inode)
        with open(output_path, "wb") as f:
            f.write(file_data)
        print(f"[green][+] Extracted file to {output_path}[/green]")


def extract_all_files(image: BinaryIO, output_dir: str):
    image.seek(BLOCK_SIZE)
    inode_bitmap_data = image.read(BLOCK_SIZE)
    inode_bitmap = [byte for byte in inode_bitmap_data]
    inode_indices = [
        index * 8 + bit
        for index, byte in enumerate(inode_bitmap)
        for bit in range(8)
        if byte & (1 << bit) != 0
    ]

    if not os.path.exists(output_dir):
        os.mkdir(output_dir)

    for inode_index in track(inode_indices, "[blue][-] Extracting files...[/blue]"):
        image.seek(BLOCK_SIZE * 2 + DISK_INODE_SIZE * inode_index)
        inode_data = image.read(DISK_INODE_SIZE)
        unpacked_data = struct.unpack(DISK_INODE_FMT, inode_data)

        inode = Inode(
            file_type=unpacked_data[0],
            size=unpacked_data[1],
            direct=unpacked_data[2:29],
            indirect1=unpacked_data[29],
            indirect2=unpacked_data[30],
            indirect3=unpacked_data[31],
        )

        if inode.file_type == 1:
            continue

        file_data = extract_inode_data(image, inode)
        file_path = os.path.join(output_dir, f"inode{inode_index}")

        with open(file_path, "wb") as file:
            file.write(file_data)

        print(f"[green][+] Extracted file to {file_path}[/green]")


@click.command()
@click.option("-i", "--image", default="fs.img", help="Path to the disk image file.")
@click.option("-o", "--output", default="output", help="Output directory.")
@click.option(
    "-m",
    "--mode",
    type=click.Choice(["restore", "extract"]),
    default="restore",
    help="Mode of operation: 'restore' to restore directory structure, 'extract' to extract all files.",
)
def cli(image: str, output: str, mode: str):
    with open(image, "rb") as img:
        super_block_data = img.read(BLOCK_SIZE)
        super_block = struct.unpack(SUPER_BLOCK_FMT, super_block_data)
        (
            magic_number,
            total_blocks,
            inode_bitmap_blocks,
            inode_area_blocks,
            data_bitmap_blocks,
            data_area_blocks,
        ) = super_block

        if magic_number != MAGIC_NUMBER:
            print(
                f"[red][!] Error: Magic number mismatch! Expected {MAGIC_NUMBER:#x}, found {magic_number:#x}[/red]"
            )
            return

        print("[blue][-] Superblock Info:[/blue]")
        print(f"    Magic Number: {magic_number:#x}")
        print(f"    Total Blocks: {total_blocks}")
        print(f"    Inode Bitmap Blocks: {inode_bitmap_blocks}")
        print(f"    Inode Area Blocks: {inode_area_blocks}")
        print(f"    Data Bitmap Blocks: {data_bitmap_blocks}")
        print(f"    Data Area Blocks: {data_area_blocks}")

        if mode == "restore":
            print("[blue][-] Restoring directory structure...[/blue]")
            extract_directory(img, 0, output)
            print("[green][+] Directory structure restored.[/green]")
        elif mode == "extract":
            print("[blue][-] Extracting all files...[/blue]")
            extract_all_files(img, output)
            print("[green][+] All files extracted.[/green]")


if __name__ == "__main__":
    cli()
