import argparse

def main():
    # Initialize parser
    parser = argparse.ArgumentParser()

    # Adding optional argument
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)

    args = parser.parse_args()
    transcode_no_bom(args.input, args.output)

def transcode_no_bom(input_path, output_path):
    print("Loading...")
    fileContent = open(input_path, mode='r', encoding='utf-8-sig').read()
    print("Encoding...")
    open(output_path, mode='w', encoding='utf-8').write(fileContent)
    print("Done")

if __name__ == '__main__':
  main()