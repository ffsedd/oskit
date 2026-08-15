sudo apt update
sudo apt install smartmontools fio -y

chmod +x ssd_benchmark.sh
./ssd_benchmark.sh /dev/sda

TEST_SIZE=2G ./ssd_benchmark.sh /dev/sda
